import { appendFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const organization = 'Eternet';
const organizationId = 89259442;
const apiUrl = 'https://api.github.com';

export const desiredSettings = Object.freeze({
  allow_merge_commit: false,
  allow_rebase_merge: false,
  allow_squash_merge: true,
  squash_merge_commit_title: 'PR_TITLE',
  squash_merge_commit_message: 'BLANK',
});

const preservedSettings = [
  'archived', 'disabled', 'visibility', 'default_branch', 'allow_auto_merge',
  'allow_update_branch', 'delete_branch_on_merge', 'merge_commit_title', 'merge_commit_message',
];

const inventoryQuery = `query($endCursor: String) {
  organization(login: "Eternet") {
    repositories(first: 100, after: $endCursor) {
      nodes {
        databaseId nameWithOwner isArchived isDisabled
        mergeCommitAllowed rebaseMergeAllowed squashMergeAllowed
        squashMergeCommitTitle squashMergeCommitMessage
      }
      totalCount
      pageInfo { hasNextPage endCursor }
    }
  }
}`;

export function createClient(token, fetchImpl = fetch) {
  if (!token?.trim()) throw new Error('A GitHub App token or REPOSITORY_SETTINGS_TOKEN is required.');
  return async (path, method = 'GET', body) => {
    const response = await fetchImpl(`${apiUrl}${path}`, {
      method,
      redirect: 'error',
      signal: AbortSignal.timeout(30_000),
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'User-Agent': 'Eternet-repository-merge-settings',
        'X-GitHub-Api-Version': '2026-03-10',
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`GitHub ${method} ${path} returned HTTP ${response.status}.`);
    const result = await response.json();
    if (result.errors?.length) throw new Error('GitHub returned GraphQL errors; the inventory is incomplete.');
    return result;
  };
}

function validateIdentity(repository) {
  if (!Number.isSafeInteger(repository.id) || repository.id <= 0 ||
      repository.owner?.id !== organizationId || repository.owner?.login !== organization ||
      !/^Eternet\/[A-Za-z0-9_.-]+$/.test(repository.full_name) ||
      ['.', '..'].includes(repository.full_name.split('/')[1])) {
    throw new Error('The repository does not have a valid Eternet identity.');
  }
}

function isConfigured(repository) {
  return Object.entries(desiredSettings).every(([key, value]) => repository[key] === value);
}

async function getInventory(client) {
  const owner = await client(`/orgs/${organization}`);
  if (owner.id !== organizationId || owner.login !== organization) throw new Error('Eternet organization identity changed.');
  const repositories = [];
  const ids = new Set();
  const cursors = new Set();
  let endCursor = null;
  let totalCount;
  do {
    const result = await client('/graphql', 'POST', { query: inventoryQuery, variables: { endCursor } });
    const connection = result.data?.organization?.repositories;
    if (!connection || !Number.isSafeInteger(connection.totalCount) || !Array.isArray(connection.nodes) ||
        typeof connection.pageInfo?.hasNextPage !== 'boolean') throw new Error('GitHub returned an invalid repository inventory.');
    totalCount ??= connection.totalCount;
    if (totalCount !== connection.totalCount) throw new Error('The repository inventory changed during pagination; rerun the workflow.');
    for (const node of connection.nodes) {
      const repository = {
        id: node?.databaseId,
        full_name: node?.nameWithOwner,
        owner: { id: owner.id, login: owner.login },
        archived: node?.isArchived,
        disabled: node?.isDisabled,
        allow_merge_commit: node?.mergeCommitAllowed,
        allow_rebase_merge: node?.rebaseMergeAllowed,
        allow_squash_merge: node?.squashMergeAllowed,
        squash_merge_commit_title: node?.squashMergeCommitTitle,
        squash_merge_commit_message: node?.squashMergeCommitMessage,
      };
      validateIdentity(repository);
      if (ids.has(repository.id)) throw new Error('GitHub returned a duplicate repository in the inventory.');
      if (['archived', 'disabled', 'allow_merge_commit', 'allow_rebase_merge', 'allow_squash_merge']
        .some(key => typeof repository[key] !== 'boolean') ||
        !['PR_TITLE', 'COMMIT_OR_PR_TITLE'].includes(repository.squash_merge_commit_title) ||
        !['BLANK', 'PR_BODY', 'COMMIT_MESSAGES'].includes(repository.squash_merge_commit_message)) {
        throw new Error('GitHub returned incomplete merge settings.');
      }
      ids.add(repository.id);
      repositories.push(repository);
    }
    if (!connection.pageInfo.hasNextPage) break;
    endCursor = connection.pageInfo.endCursor;
    if (!endCursor || cursors.has(endCursor)) throw new Error('GitHub returned an invalid pagination cursor.');
    cursors.add(endCursor);
  } while (true);
  if (repositories.length !== totalCount || totalCount === 0) throw new Error('The repository inventory is incomplete or empty.');
  return repositories;
}

export async function reconcile({ client, dryRun = false, log = console.log }) {
  const repositories = await getInventory(client);
  const results = [];
  for (const repository of repositories) {
    let status;
    let detail;
    try {
      if (repository.archived || repository.disabled) {
        status = 'skipped';
      } else if (isConfigured(repository)) {
        status = 'alreadyConfigured';
      } else {
        const path = `/repos/${organization}/${encodeURIComponent(repository.full_name.split('/')[1])}`;
        const before = await client(path);
        validateIdentity(before);
        if (before.id !== repository.id || before.full_name !== repository.full_name) throw new Error('Repository identity changed before the update.');
        if (before.archived || before.disabled) {
          status = 'skipped';
        } else if (isConfigured(before)) {
          status = 'alreadyConfigured';
        } else if (dryRun) {
          status = 'wouldUpdate';
        } else {
          await client(path, 'PATCH', desiredSettings);
          const after = await client(path);
          validateIdentity(after);
          if (after.id !== before.id || after.full_name !== before.full_name || !isConfigured(after)) {
            throw new Error('The read-back did not confirm the requested merge settings.');
          }
          for (const key of preservedSettings) {
            if (after[key] !== before[key]) throw new Error(`An unrelated setting changed during the update: ${key}.`);
          }
          status = 'updatedVerified';
        }
      }
    } catch (error) {
      status = 'failed';
      detail = error.message;
    }
    results.push({ repository: repository.full_name, status, ...(detail ? { detail } : {}) });
    if (!['alreadyConfigured', 'skipped'].includes(status)) log(`${repository.full_name}: ${status}${detail ? ` (${detail})` : ''}`);
  }
  return { dryRun, inspected: repositories.length, results };
}

async function main() {
  if (process.argv.slice(2).some(argument => argument !== '--dry-run')) throw new Error('Only --dry-run is supported.');
  const dryRun = process.argv.includes('--dry-run') || process.env.DRY_RUN === 'true';
  const report = await reconcile({ client: createClient(process.env.GH_TOKEN), dryRun });
  const counts = { updatedVerified: 0, alreadyConfigured: 0, skipped: 0, wouldUpdate: 0, failed: 0 };
  for (const result of report.results) counts[result.status]++;
  const summary = [
    '## Eternet repository merge settings',
    '',
    `Mode: ${dryRun ? 'preview (no changes)' : 'apply'}`,
    `Repositories inspected: ${report.inspected}`,
    ...Object.entries(counts).map(([status, count]) => `- ${status}: ${count}`),
    ...report.results.filter(result => result.status === 'failed').map(result => `- ${result.repository}: ${result.detail}`),
    '',
  ].join('\n');
  console.log(summary);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, summary);
  if (counts.failed) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
