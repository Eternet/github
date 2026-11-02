# Self-hosted CI runners

Repository-settings validation and reconciliation select Eternet Linux runners.
The reusable .NET and editorconfig workflows already select Eternet Windows runners.
Linux workers must provide bash, Git and Node.js 20 or later. Missing prerequisites
must fail or leave jobs queued, without installing host packages or using a hosted
fallback. The reconciliation scope and credentials remain unchanged.

Run `node --test scripts/reconcile-merge-settings.test.mjs` for synthetic checks;
this does not modify organization or repository settings. Organization prohibition
of hosted runners and cross-repository verification are tracked in Eternet/Servidores#559.
