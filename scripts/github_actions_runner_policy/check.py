#!/usr/bin/env python3
"""Fail-closed static audit of every possible GitHub Actions runner selector."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import sys

import yaml


class PolicyError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Keep YAML's `on` a string and reject ambiguous duplicate mapping keys."""
    yaml_implicit_resolvers = {
        key: [(tag, rx) for tag, rx in resolvers if tag != 'tag:yaml.org,2002:bool']
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise PolicyError(f'duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
UniqueLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|false)$', re.I), list('tTfF'))


def load_yaml(content):
    try:
        value = yaml.load(content, Loader=UniqueLoader)
    except (yaml.YAMLError, TypeError) as exc:
        raise PolicyError(f'invalid YAML: {exc}') from exc
    if not isinstance(value, dict):
        raise PolicyError('workflow must be a YAML mapping')
    return value


TOKEN = re.compile(r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<op>\&\&|\|\||==|!=|[(),])|(?P<name>[A-Za-z_][A-Za-z_0-9.]*)|(?P<number>-?\d+(?:\.\d+)?))")


class Expression:
    """Only the small, finite expression language used by audited workflows."""
    def __init__(self, text):
        self.tokens = []
        offset = 0
        while offset < len(text):
            match = TOKEN.match(text, offset)
            if not match:
                if not text[offset:].strip():
                    break
                raise PolicyError(f'unsupported expression syntax: {text[offset:]}')
            self.tokens.append((match.lastgroup, match.group(match.lastgroup)))
            offset = match.end()
        self.index = 0
        self.tree = self.parse(0)
        if self.index != len(self.tokens):
            raise PolicyError('unsupported trailing expression tokens')

    def peek(self, token=None):
        return self.index < len(self.tokens) and (token is None or self.tokens[self.index][1] == token)

    def pop(self, token=None):
        if not self.peek(token):
            raise PolicyError(f'expected {token or "expression token"}')
        value = self.tokens[self.index]
        self.index += 1
        return value

    def parse(self, level):
        operators = [('||',), ('&&',), ('==', '!=')]
        if level < len(operators):
            left = self.parse(level + 1)
            while self.peek() and self.tokens[self.index][1] in operators[level]:
                op = self.pop()[1]
                left = (op, left, self.parse(level + 1))
            return left
        kind, value = self.pop()
        if value == '(':
            result = self.parse(0)
            self.pop(')')
            return result
        if kind == 'string':
            return ('literal', value[1:-1].replace("''", "'"))
        if kind == 'number':
            return ('literal', json.loads(value))
        if kind != 'name':
            raise PolicyError(f'unexpected expression token {value}')
        if value.lower() in ('true', 'false', 'null'):
            return ('literal', json.loads(value.lower()))
        if self.peek('('):
            self.pop('(')
            args = []
            if not self.peek(')'):
                args.append(self.parse(0))
                while self.peek(','):
                    self.pop(',')
                    args.append(self.parse(0))
            self.pop(')')
            if value.lower() not in ('fromjson', 'format', 'contains'):
                raise PolicyError(f'unsupported expression function {value}')
            return ('call', value.lower(), args)
        return ('variable', value)

    def variables(self):
        def walk(node):
            if node[0] == 'variable':
                return {node[1]}
            if node[0] == 'call':
                return set().union(*(walk(arg) for arg in node[2]))
            if node[0] in ('&&', '||', '==', '!='):
                return walk(node[1]) | walk(node[2])
            return set()
        return walk(self.tree)

    def comparison_values(self, variable):
        """Partition unbounded strings only when every use is literal equality."""
        values = []
        def walk(node, parent=None):
            if node[0] == 'variable' and node[1] == variable:
                if parent is None or parent[0] not in ('==', '!='):
                    raise PolicyError(f'unbounded input {variable} escapes literal equality')
                other = parent[2] if parent[1] is node else parent[1]
                if other[0] != 'literal' or not isinstance(other[1], str):
                    raise PolicyError(f'unbounded input {variable} compares to a nonliteral string')
                values.append(other[1])
            elif node[0] == 'call':
                for arg in node[2]:
                    walk(arg, node)
            elif node[0] in ('&&', '||', '==', '!='):
                walk(node[1], node)
                walk(node[2], node)
        walk(self.tree)
        return values

    def evaluate(self, context):
        def run(node):
            kind = node[0]
            if kind == 'literal':
                return node[1]
            if kind == 'variable':
                return context[node[1]]
            if kind == '&&':
                left = run(node[1])
                return run(node[2]) if truthy(left) else left
            if kind == '||':
                left = run(node[1])
                return left if truthy(left) else run(node[2])
            if kind in ('==', '!='):
                a, b = run(node[1]), run(node[2])
                equal = equal_values(a, b)
                return equal if kind == '==' else not equal
            name = node[1]
            args = [run(arg) for arg in node[2]]
            if name == 'fromjson' and len(args) == 1:
                return json.loads(args[0])
            if name == 'format' and args:
                if any(not isinstance(arg, str) for arg in args):
                    raise PolicyError('format arguments must be strings; primitive coercion is unverified')
                return args[0].format(*args[1:])
            if name == 'contains' and len(args) == 2:
                container, item = args
                if isinstance(container, list):
                    return any(equal_values(candidate, item) for candidate in container)
                if not isinstance(container, str) or not isinstance(item, str):
                    raise PolicyError('scalar contains arguments must be strings; primitive coercion is unverified')
                return item.lower() in container.lower()
            raise PolicyError(f'unsupported arguments for {name}')
        try:
            return run(self.tree)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise PolicyError(f'expression cannot be evaluated: {exc}') from exc


def truthy(value):
    return value is not None and value is not False and not (isinstance(value, (str, int, float)) and value in ('', 0))


def equal_values(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    if type(a) is type(b):
        # Arrays/objects compare by instance in GitHub expressions.
        return a is b if isinstance(a, (list, dict)) else a == b
    if isinstance(a, str) or isinstance(b, str):
        raise PolicyError('mixed string/primitive equality requires unverified numeric coercion')
    def number(value):
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        return math.nan
    return number(a) == number(b)


OWN_LABELS = {'self-hosted', 'mini-pc-perf', 'minipc-perf-1', 'minipc-perf-2', 'minipc-perf-3', 'minipc-perf-4', 'copilot-arc'}
OWN_GROUPS = {'minipc-perf'}
HOSTED = re.compile(r'^(?:ubuntu-|windows-|macos-|github-hosted(?:$|-))', re.I)
REMOTE = re.compile(r'^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(.github/workflows/[^@]+\.ya?ml)@([a-fA-F0-9]{40})$')
LIMIT = 4096


def expression_spans(value):
    """Find closing braces outside quoted strings (format JSON contains `}}`)."""
    spans = []
    offset = 0
    while (start := value.find('${{', offset)) >= 0:
        index = start + 3
        quoted = False
        while index < len(value):
            if value[index] == "'":
                if quoted and value[index:index + 2] == "''":
                    index += 2
                    continue
                quoted = not quoted
            if not quoted and value[index:index + 2] == '}}':
                spans.append((start, index + 2, value[start + 3:index]))
                offset = index + 2
                break
            index += 1
        else:
            raise PolicyError('malformed expression')
    return spans


def expressions(value):
    if isinstance(value, str):
        return [Expression(text) for _, _, text in expression_spans(value)]
    if isinstance(value, list):
        return [expr for item in value for expr in expressions(item)]
    if isinstance(value, dict):
        return [expr for item in value.values() for expr in expressions(item)]
    return []


def resolve(value, context):
    if isinstance(value, str):
        spans = expression_spans(value)
        if len(spans) == 1 and not value[:spans[0][0]].strip() and not value[spans[0][1]:].strip():
            return Expression(spans[0][2]).evaluate(context)
        result = value
        for start, end, text in reversed(spans):
            result = result[:start] + str(Expression(text).evaluate(context)) + result[end:]
        return result
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, context) for key, item in value.items()}
    return value


def matrix_rows(job):
    matrix = job.get('strategy', {}).get('matrix', {})
    if not isinstance(matrix, dict):
        raise PolicyError('matrix must be a static mapping; dynamic matrices are unverified')
    axes = {key: value for key, value in matrix.items() if key not in ('include', 'exclude')}
    if any(not isinstance(value, list) or not value for value in axes.values()):
        raise PolicyError('matrix axes must be nonempty literal lists')
    if expressions(matrix):
        raise PolicyError('matrix expressions have no verified finite provenance')
    size = 1
    for values in axes.values():
        size *= len(values)
    if size > LIMIT:
        raise PolicyError('matrix exceeds bounded audit limit')
    originals = [dict(zip(axes, values)) for values in itertools.product(*axes.values())] if axes else []
    rows = [dict(row) for row in originals]
    for kind in ('include', 'exclude'):
        if not isinstance(matrix.get(kind, []), list) or any(not isinstance(entry, dict) for entry in matrix.get(kind, [])):
            raise PolicyError(f'matrix {kind} must be a literal list of mappings')
    for excluded in matrix.get('exclude', []):
        rows = [row for row in rows if not all(row.get(key) == value for key, value in excluded.items())]
    # Include merges only when it does not overwrite an original axis value.
    base_rows = list(rows)
    for included in matrix.get('include', []):
        merged = False
        for row in base_rows:
            if all(key not in axes or row.get(key) == value for key, value in included.items()):
                row.update(included)
                merged = True
        if not merged:
            rows.append(dict(included))
    if not axes and not matrix.get('include'):
        return [{}]
    return rows


def string_partition(variable, parsed_expressions):
    values = [value for expr in parsed_expressions for value in expr.comparison_values(variable)]
    if not values:
        raise PolicyError(f'{variable} has no verified literal string equality partition')
    sentinel = '__runner_policy_other__'
    while sentinel.lower() in {value.lower() for value in values}:
        sentinel += '_'
    return values + ['', sentinel]


def contexts(workflow, job, selector):
    parsed_expressions = expressions(selector)
    variables = set().union(*(expr.variables() for expr in parsed_expressions))
    events = workflow.get('on', {})
    if isinstance(events, str):
        event_names = [events]
        events = {events: {}}
    elif isinstance(events, list):
        event_names = events
        events = {event: {} for event in events}
    elif isinstance(events, dict):
        event_names = list(events)
    else:
        raise PolicyError('unsupported event declaration')
    domains = {}
    for variable in variables:
        if variable.startswith('matrix.'):
            continue
        if variable == 'github.event_name':
            if 'workflow_call' in events:
                raise PolicyError('event-dependent reusable selector has no verified caller event domain')
            domains[variable] = event_names
            continue
        if variable.startswith('vars.'):
            if not re.fullmatch(r'vars\.[A-Za-z_][A-Za-z_0-9]*', variable):
                raise PolicyError(f'unverified configuration variable property {variable}')
            # GitHub configuration variables are strings (unset is empty).
            # Every use must still be a literal equality; the value can never
            # escape into a selector, JSON, or free interpolation.
            domains[variable] = string_partition(variable, parsed_expressions)
            continue
        if not variable.startswith('inputs.'):
            raise PolicyError(f'unverified expression variable {variable}')
        name = variable[len('inputs.'):]
        # workflow_call string inputs are not bounded by dispatch choices.
        call = (events.get('workflow_call') or {}).get('inputs', {}).get(name)
        dispatch = (events.get('workflow_dispatch') or {}).get('inputs', {}).get(name)
        definitions = [definition for definition in (dispatch, call) if definition is not None]
        if not definitions or any(not isinstance(definition, dict) for definition in definitions):
            raise PolicyError(f'input {name} has no verified finite definition')
        declared_types = {definition.get('type', 'string') for definition in definitions}
        unsupported = declared_types - {'string', 'choice', 'boolean'}
        if unsupported:
            raise PolicyError(f'input {name} type {sorted(unsupported)[0]!r} has no verified finite domain')
        if 'boolean' in declared_types and declared_types != {'boolean'}:
            raise PolicyError(f'input {name} has incompatible dispatch/call types; no shared finite domain')
        if declared_types == {'boolean'}:
            values = [False, True]
        elif call is None and dispatch.get('type') == 'choice':
            values = dispatch.get('options')
            if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
                raise PolicyError(f'input {name} choices must be nonempty literal strings')
        else:
            values = string_partition(variable, parsed_expressions)
        if any(event != 'workflow_dispatch' for event in event_names):
            values.append('')
        domains[variable] = values
    count = 0
    # Matrix values cannot affect a selector that never reads matrix.*. Even a
    # runtime-generated matrix then repeats the same statically proved runner.
    rows = matrix_rows(job) if any(variable.startswith('matrix.') for variable in variables) else [{}]
    for row in rows:
        for values in itertools.product(*domains.values()):
            count += 1
            if count > LIMIT:
                raise PolicyError('expression combinations exceed bounded audit limit')
            context = dict(zip(domains, values))
            context.update({f'matrix.{key}': value for key, value in row.items()})
            yield context


def check_selector(selector):
    group = None
    if isinstance(selector, dict):
        if set(selector) - {'group', 'labels'}:
            raise PolicyError('unsupported runs-on mapping keys')
        group = selector.get('group')
        if 'group' in selector and (not isinstance(group, str) or not group.strip()):
            raise PolicyError(f'invalid runner group {group!r}')
        selector = selector.get('labels', [])
    labels = [selector] if isinstance(selector, str) else selector
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise PolicyError('runner labels must be nonempty literal strings')
    if any(HOSTED.match(label.strip()) for label in labels):
        raise PolicyError(f'GitHub-hosted runner label is forbidden: {labels!r}')
    if group and group.lower() not in OWN_GROUPS and not any(label.lower() == 'self-hosted' for label in labels):
        raise PolicyError(f'unverified runner group {group!r} without reserved self-hosted label')
    if not group and not any(label.lower() in OWN_LABELS for label in labels):
        raise PolicyError(f'runner selector has no verified Eternet provenance: {labels!r}')


class Auditor:
    def __init__(self, root, reusables=None):
        self.root = Path(root).resolve()
        self.reusables = reusables or {}
        self.visited = set()
        self.errors = []
        self.jobs = 0

    def workflow(self, content, name, stack=()):
        if name in stack:
            raise PolicyError(f'reusable workflow cycle at {name}')
        if name in self.visited:
            return
        self.visited.add(name)
        workflow = load_yaml(content)
        jobs = workflow.get('jobs')
        if not isinstance(jobs, dict) or not jobs:
            raise PolicyError('workflow has no jobs mapping')
        for job_name, job in jobs.items():
            location = f'{name}: jobs.{job_name}'
            try:
                if not isinstance(job, dict):
                    raise PolicyError('job must be a mapping')
                self.jobs += 1
                if 'uses' in job:
                    if 'runs-on' in job:
                        raise PolicyError('reusable job cannot also declare runs-on')
                    self.reusable(job['uses'], stack + (name,))
                elif 'runs-on' in job:
                    selector = job['runs-on']
                    for context in contexts(workflow, job, selector):
                        check_selector(resolve(selector, context))
                else:
                    raise PolicyError('job has neither runs-on nor reusable uses')
            except (PolicyError, TypeError, AttributeError) as exc:
                self.errors.append(f'{location}: {exc}')

    def reusable(self, reference, stack):
        if not isinstance(reference, str):
            raise PolicyError('reusable workflow reference must be literal')
        if reference.startswith('./'):
            # A local reference inside a remote reusable belongs to that remote
            # repository at the same immutable revision, never to the caller.
            remote_parent = REMOTE.fullmatch(stack[-1]) if stack else None
            if remote_parent:
                self.reusable(f'{remote_parent[1]}/{reference[2:]}@{remote_parent[3]}', stack)
                return
            path = (self.root / reference[2:]).resolve()
            if not path.is_relative_to(self.root / '.github' / 'workflows') or path.suffix not in ('.yml', '.yaml') or not path.is_file():
                raise PolicyError(f'invalid local reusable reference {reference}')
            self.workflow(path.read_text(encoding='utf-8-sig'), str(path.relative_to(self.root)), stack)
            return
        if not REMOTE.fullmatch(reference):
            raise PolicyError(f'remote reusable must use an audited immutable 40-character commit: {reference}')
        snapshot = self.reusables.get(reference)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('content'), str):
            raise PolicyError(f'remote reusable has no centrally audited snapshot: {reference}')
        if hashlib.sha256(snapshot['content'].encode()).hexdigest() != snapshot.get('sha256'):
            raise PolicyError(f'remote reusable snapshot digest mismatch: {reference}')
        self.workflow(snapshot['content'], reference, stack)

    def run(self):
        paths = sorted((self.root / '.github' / 'workflows').glob('*.y*ml'))
        if not paths:
            self.errors.append('no workflows found')
        for path in paths:
            try:
                self.workflow(path.read_text(encoding='utf-8-sig'), str(path.relative_to(self.root)))
            except (PolicyError, OSError) as exc:
                self.errors.append(f'{path.name}: {exc}')
        return self.errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    manifest = Path(__file__).with_name('approved_reusables.json')
    auditor = Auditor(args.root, json.loads(manifest.read_text(encoding='utf-8')))
    for error in auditor.run():
        print(f'ERROR: {error}', file=sys.stderr)
    if auditor.errors:
        print(f'Runner policy failed with {len(auditor.errors)} violation(s).', file=sys.stderr)
        return 1
    print(f'Runner policy passed: {len(auditor.visited)} workflows, {auditor.jobs} jobs; only verified Eternet selectors.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
