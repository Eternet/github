# Configuración automática de merges

El workflow `Reconcile repository merge settings` revisa los repositorios de
Eternet cada 15 minutos y configura únicamente estos cinco ajustes cuando no
coinciden con la política:

| Ajuste | Valor |
| --- | --- |
| Allow merge commits | Deshabilitado |
| Allow rebase merging | Deshabilitado |
| Allow squash merging | Habilitado |
| Squash commit title | `PR_TITLE` |
| Squash commit message | `BLANK` |

Los dos últimos valores corresponden a **Pull request title** en el selector de
GitHub. El workflow incluye repos nuevos y corrige cambios en estos ajustes de
repos existentes. Omite repos archivados y deshabilitados. No crea repositorios,
no modifica contenidos ni desarchiva repos.

La enumeración usa GraphQL con paginación. Antes de cada cambio se vuelve a leer
el repositorio por REST para comprobar su identidad y estado. El PATCH contiene
solo los cinco ajustes de la tabla; una lectura posterior verifica el resultado
y comprueba que los demás ajustes seleccionados no hayan cambiado.

## Activación

El archivo del workflow debe estar en `main`. Configurar una de estas credenciales
en **Eternet/github → Settings → Secrets and variables → Actions**:

1. **GitHub App (recomendada):** instalar una App en Eternet con acceso a **All
   repositories**, incluyendo futuros repos, y permiso de repositorio
   **Administration: Read and write**. Guardar su ID en la variable
   `REPOSITORY_SETTINGS_APP_ID` y su clave privada en el secret
   `REPOSITORY_SETTINGS_APP_PRIVATE_KEY`. El workflow crea un token temporal
   limitado a `Administration: write` y lo revoca al finalizar.
2. **Token existente:** guardar un token con acceso administrativo a todos los
   repositorios de Eternet en `REPOSITORY_SETTINGS_TOKEN`. Un fine-grained PAT debe
   tener Eternet como resource owner, acceso a **All repositories** y permiso
   **Administration: Read and write**. Si tiene vencimiento, renovarlo antes de
   esa fecha. Para un PAT classic/OAuth, el scope `repo` y la pertenencia con
   permisos admin dan acceso a los ajustes, pero su alcance puede ser mayor.

El `GITHUB_TOKEN` propio del workflow solo tiene acceso al repo donde se ejecuta;
no alcanza para administrar los demás repositorios de la organización. No subir
tokens ni claves privadas al código ni imprimirlos en logs.

Luego, ejecutar **Actions → Reconcile repository merge settings → Run workflow**
con `dry_run=true` para inspeccionar el resumen. Para aplicar en una ejecución
manual, desmarcar `dry_run`. Las ejecuciones programadas aplican los cambios.

La programación usa los minutos 7, 22, 37 y 52 de cada hora para evitar el inicio
de hora. GitHub puede demorar una ejecución programada; no es un disparador
instantáneo al crear el repo. Los fallos quedan reflejados en el estado y resumen
de la ejecución, y la próxima ejecución vuelve a inspeccionar el estado actual.

## Validación local

Con Node.js 20 o posterior:

```sh
node --test scripts/reconcile-merge-settings.test.mjs
node scripts/reconcile-merge-settings.mjs --dry-run
```

La segunda instrucción requiere `GH_TOKEN` en el entorno y solo realiza lecturas.
Las pruebas cubren paginación, repos nuevos, omisión de archivados/deshabilitados,
vista previa, transferencias, verificación posterior y errores de API. Las
pruebas usan respuestas sintéticas; la vista previa permite verificar la conexión
real y los repos accesibles sin modificarlos.

Referencias: [eventos programados de GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[API de ajustes de repositorios](https://docs.github.com/en/rest/repos/repos#update-a-repository),
[token de GitHub Actions](https://docs.github.com/en/actions/concepts/security/github_token),
[actions/create-github-app-token](https://github.com/actions/create-github-app-token).
