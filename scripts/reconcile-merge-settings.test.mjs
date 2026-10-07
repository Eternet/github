import assert from 'node:assert/strict';
import test from 'node:test';
import { createClient, desiredSettings, reconcile } from './reconcile-merge-settings.mjs';

function repository(id, name, configured = false, extras = {}) {
  return {
    id, full_name: `Eternet/${name}`, owner: { id: 89259442, login: 'Eternet' },
    archived: false, disabled: false, visibility: 'private', default_branch: 'main',
    allow_auto_merge: false, allow_update_branch: false, delete_branch_on_merge: false,
    merge_commit_title: 'MERGE_MESSAGE', merge_commit_message: 'PR_TITLE',
    allow_merge_commit: true, allow_rebase_merge: true, allow_squash_merge: true,
    squash_merge_commit_title: 'COMMIT_OR_PR_TITLE', squash_merge_commit_message: 'COMMIT_MESSAGES',
    ...(configured ? desiredSettings : {}), ...extras,
  };
}

function page(repositories, nextCursor = null, totalCount = repositories.length) {
  return { data: { organization: { repositories: {
    nodes: repositories.map(repo => ({
      databaseId: repo.id, nameWithOwner: repo.full_name,
      isArchived: repo.archived, isDisabled: repo.disabled,
      mergeCommitAllowed: repo.allow_merge_commit, rebaseMergeAllowed: repo.allow_rebase_merge,
      squashMergeAllowed: repo.allow_squash_merge, squashMergeCommitTitle: repo.squash_merge_commit_title,
      squashMergeCommitMessage: repo.squash_merge_commit_message,
    })),
    totalCount, pageInfo: { hasNextPage: nextCursor !== null, endCursor: nextCursor },
  } } } };
}

function fixture(repositories, { pages = [page(repositories)], onGet, onPatch } = {}) {
  const requests = [];
  const states = new Map(repositories.map(repo => [repo.full_name, structuredClone(repo)]));
  let pageIndex = 0;
  const client = async (path, method = 'GET', body) => {
    requests.push({ path, method, body });
    if (path === '/orgs/Eternet') return { id: 89259442, login: 'Eternet' };
    if (path === '/graphql') return pages[pageIndex++];
    const name = path.replace('/repos/', '');
    const state = states.get(name);
    assert.ok(state, `Unexpected repository: ${name}`);
    if (method === 'PATCH') {
      if (onPatch) onPatch(state, body);
      else Object.assign(state, body);
      return structuredClone(state);
    }
    if (onGet) onGet(state, requests);
    return structuredClone(state);
  };
  return { client, requests, states };
}

test('scans every page, updates a new repository, and skips configured, archived and disabled repositories', async () => {
  const existing = repository(1, 'Existing', true);
  const archived = repository(2, 'Archived', false, { archived: true });
  const disabled = repository(3, 'Disabled', false, { disabled: true });
  const created = repository(4, 'New');
  const mock = fixture([existing, archived, disabled, created], {
    pages: [page([existing, archived, disabled], 'page2', 4), page([created], null, 4)],
  });
  const report = await reconcile({ client: mock.client, log: () => {} });
  assert.equal(report.inspected, 4);
  assert.deepEqual(report.results.map(result => result.status), ['alreadyConfigured', 'skipped', 'skipped', 'updatedVerified']);
  assert.equal(mock.requests.filter(request => request.method === 'PATCH').length, 1);
  assert.deepEqual(mock.requests.find(request => request.method === 'PATCH').body, desiredSettings);
  assert.deepEqual(mock.states.get(created.full_name), { ...created, ...desiredSettings });
  assert.equal(mock.requests.filter(request => request.path === '/repos/Eternet/New' && request.method === 'GET').length, 2);
  assert.equal(mock.requests.filter(request => request.path === '/graphql')[1].body.variables.endCursor, 'page2');
});

test('preview enumerates candidates without writing', async () => {
  const mock = fixture([repository(1, 'New')]);
  const report = await reconcile({ client: mock.client, dryRun: true, log: () => {} });
  assert.equal(report.results[0].status, 'wouldUpdate');
  assert.equal(mock.requests.filter(request => request.method === 'PATCH').length, 0);
});

test('refuses to update a transferred repository and continues with other Eternet repositories', async () => {
  const moved = repository(1, 'Moved');
  const other = repository(2, 'New');
  const mock = fixture([moved, other], { onGet: state => {
    if (state.id === moved.id) state.owner = { id: 999, login: 'Other' };
  } });
  const report = await reconcile({ client: mock.client, log: () => {} });
  assert.deepEqual(report.results.map(result => result.status), ['failed', 'updatedVerified']);
  assert.deepEqual(mock.requests.filter(request => request.method === 'PATCH').map(request => request.path), ['/repos/Eternet/New']);
});

test('does not patch a repository that was archived after inventory collection', async () => {
  const mock = fixture([repository(1, 'New')], { onGet: state => { state.archived = true; } });
  const report = await reconcile({ client: mock.client, log: () => {} });
  assert.equal(report.results[0].status, 'skipped');
  assert.equal(mock.requests.filter(request => request.method === 'PATCH').length, 0);
});

test('reports failure when GitHub does not persist the requested settings', async () => {
  const mock = fixture([repository(1, 'New')], { onPatch: () => {} });
  const report = await reconcile({ client: mock.client, log: () => {} });
  assert.equal(report.results[0].status, 'failed');
  assert.match(report.results[0].detail, /read-back/);
});

test('detects unrelated setting changes during the update', async () => {
  const mock = fixture([repository(1, 'New')], { onPatch: (state, body) => {
    Object.assign(state, body, { visibility: 'public' });
  } });
  const report = await reconcile({ client: mock.client, log: () => {} });
  assert.equal(report.results[0].status, 'failed');
  assert.match(report.results[0].detail, /visibility/);
});

test('incomplete, duplicate, malformed and changing inventories cannot produce writes', async t => {
  const repo = repository(1, 'New');
  const malformed = page([repo]);
  malformed.data.organization.repositories.nodes[0].squashMergeAllowed = null;
  const cases = [
    ['missing page', [page([repo], null, 2)]],
    ['duplicate', [page([repo], 'next', 2), page([repo], null, 2)]],
    ['repeated cursor', [page([], 'next', 1), page([], 'next', 1)]],
    ['changing count', [page([], 'next', 1), page([repo], null, 2)]],
    ['malformed settings', [malformed]],
  ];
  for (const [name, pages] of cases) await t.test(name, async () => {
    const mock = fixture([repo], { pages });
    await assert.rejects(reconcile({ client: mock.client, log: () => {} }));
    assert.equal(mock.requests.filter(request => request.method === 'PATCH').length, 0);
  });
});

test('rejects a different organization before reading or writing repositories', async () => {
  const calls = [];
  const client = async path => { calls.push(path); return { id: 999, login: 'Eternet' }; };
  await assert.rejects(reconcile({ client }), /identity changed/);
  assert.deepEqual(calls, ['/orgs/Eternet']);
});

test('HTTP authentication stays on GitHub and failures do not expose the token', async () => {
  const token = 'test-only-token';
  let request;
  const client = createClient(token, async (url, options) => {
    request = { url, options };
    return new Response('{}', { status: 403 });
  });
  await assert.rejects(client('/orgs/Eternet'), error => error.message.includes('HTTP 403') && !error.message.includes(token));
  assert.equal(request.url, 'https://api.github.com/orgs/Eternet');
  assert.equal(request.options.redirect, 'error');
  assert.equal(request.options.headers.Authorization, `Bearer ${token}`);
});

test('GraphQL errors with HTTP 200 are rejected', async () => {
  const client = createClient('test-only-token', async () => new Response(JSON.stringify({ errors: [{ message: 'denied' }] }), { status: 200 }));
  await assert.rejects(client('/graphql', 'POST', {}), /GraphQL errors/);
});
