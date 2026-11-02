# Política compartida de runners propios

Este control rechaza selectores hospedados por GitHub y selectores cuya
procedencia no puede probar estáticamente. No despacha workflows ni modifica
runners, DB, IIS o servicios. El control de CI detecta cambios; **no bloquea por
sí solo que otro job hospedado arranque en paralelo**. La prevención a nivel de
organización requiere deshabilitar los runners estándar hospedados para todos
los repositorios. El permiso de la identidad disponible respondió HTTP 403;
esa configuración continúa pendiente de un propietario con acceso a Actions
policies, bajo seguimiento en [Servidores #559](https://github.com/Eternet/Servidores/issues/559).

## Uso

Desde un checkout del repositorio a revisar, con Python 3.10+ y PyYAML 6.0.3:

```powershell
python C:/ruta/github/scripts/github_actions_runner_policy/check.py --root .
python -m unittest discover -s C:/ruta/github/scripts/github_actions_runner_policy -p 'test_*.py' -v
```

Antes de publicar o repetir CI, auditar el **commit fuente completo**, no solo
el diff, el editor ni la rama principal:

```powershell
python C:/ruta/github/scripts/github_actions_runner_policy/check.py --root . --git-ref HEAD
python C:/ruta/github/scripts/github_actions_runner_policy/check.py --root . --git-ref refs/heads/rama-a-publicar
```

El modo `--git-ref` resuelve un commit exacto y lee todos los blobs de
`.github/workflows` desde ese árbol, incluidos los reutilizables locales. No hace
checkout ni ejecuta código del árbol revisado; ignora cambios del editor/index,
atributos `export-ignore` y reemplazos locales `git replace`. Un commit válido
sin workflows pasa con 0 workflows/0 jobs. Un ref inexistente, una historia u
objeto faltante, un archivo requerido ausente o una raíz sin repositorio fallan
sin recurrir al editor. El modo de editor sin `--git-ref` sigue siendo solo una
comprobación de desarrollo y rechaza una raíz sin workflows.

Para `workflow_dispatch`, obtener y auditar el ref que realmente se enviará a
`gh workflow run --ref`, y comprobar que el ref remoto sigue apuntando al SHA
que imprimió el control justo antes de despacharlo. Si se movió, actualizar la
referencia y repetir la auditoría. Una auditoría de `main` no autoriza disparar
otro branch/tag ni volver a ejecutar un run histórico.

## Integración previa al push

`pre_push.py` consume el stdin nativo de Git, con una línea por actualización:

```text
<local-ref> <local-object> <remote-ref> <remote-object>
```

La entrada del hook debe invocar el helper desde una copia central confiable,
con `check.py` y `approved_reusables.json` del mismo commit integrado:

```text
python C:/ruta/github/scripts/github_actions_runner_policy/pre_push.py --root <repositorio> <remote-name> <remote-url>
```

Audita cada commit saliente de branches y tags, aunque no sean el `HEAD`
actual. También admite `HEAD` explícito y tags anotados que apunten a commits.
Comprueba que cada ref local todavía coincide con el objeto informado por Git;
rechaza fuentes desconocidas, refs arbitrarios, tags sin commit, entradas
malformadas e historia no disponible. Omite eliminaciones y acepta un push sin
actualizaciones. Varios refs al mismo commit comparten la auditoría, pero todos
los refs y objetos se comprueban. La URL del remote no se imprime.

Este cambio entrega el helper y **no instala ni reemplaza hooks**. La instalación
debe revisar `core.hooksPath` y conservar/encadenar cualquier hook preexistente.
Un hook local protege los pushes que pasan por él; no controla otros equipos,
la edición web o llamadas a la API. Mantener la prohibición de runners estándar
en la organización como barrera independiente.

Los consumidores usan la misma acción compuesta, sin copiar el validador.
Después de integrar el cambio de infraestructura, reemplazar `COMMIT_INTEGRADO`
por un SHA completo alcanzable desde la rama principal de Eternet/github:

```yaml
jobs:
  runner-policy:
    runs-on: [self-hosted, Linux]
    steps:
      - uses: actions/checkout@v4
      - uses: Eternet/github/scripts/github_actions_runner_policy@COMMIT_INTEGRADO
```

La acción requiere un runner Linux propio con bash y los prerrequisitos de
`actions/setup-python`. Instala PyYAML en un entorno virtual temporal y lo elimina
al finalizar; no instala paquetes globales. La acción vive en Eternet/github, un repositorio público, para permitir el uso
desde los repositorios de la organización sin compartir acciones privadas.
El workflow `self-hosted-runner-policy.yml` comprueba el propio Eternet/github en
push/PR. La interfaz compartida entre repositorios es la acción compuesta.
La acción audita `HEAD` del checkout comprometido mediante `--git-ref HEAD`.

## Procedencia admitida

- `self-hosted` es la etiqueta reservada de runners propios. Puede combinarse
  con etiquetas de SO/capacidad. Una etiqueta hospedada (`ubuntu-*`,
  `windows-*`, `macos-*`, `github-hosted`) se rechaza incluso en ese arreglo.
  También prueba propiedad dentro de un grupo literal (por ejemplo `default`)
  sin cambiar la selección de grupo existente. Un grupo desconocido sin esa
  etiqueta reservada no se admite.
- Infraestructura propia registrada con `--no-default-labels`: `mini-pc-perf`,
  `minipc-perf-1` a `minipc-perf-4`, o el grupo `MiniPC-Perf`.
- El scale set ARC propio `copilot-arc`.

Estas excepciones corresponden al inventario de infraestructura comprobado
durante #559; no se infiere propiedad de cualquier etiqueta o grupo nuevo.
Agregar otra excepción requiere verificar previamente su registro e incluir
su evidencia en el PR de infraestructura. Una etiqueta adicional combinada
con `self-hosted` restringe un runner propio; nunca habilita uno hospedado.

## Comportamiento y límites

Se revisan todos los workflows y jobs, incluso los deshabilitados con `if:
false`. Se rechazan claves YAML duplicadas. Se expanden matrices literales con
`include`/`exclude`; una inclusión independiente nunca sobrescribe otra anterior.
Se admite un subconjunto explícito de expresiones: entradas choice/boolean,
`matrix.*`, `github.event_name`, `&&`, `||`, `==`, `!=`, `fromJSON`, `format` y
`contains`. Todas las ramas finitas se comprueban, sin usar el valor por defecto
como sustituto de la entrada real. Un string sin límite solo se admite si cada
uso es una comparación de igualdad/desigualdad con un string literal: se prueban
todos los literales y un representante del resto. Nunca se admite ese string
como etiqueta, interpolación libre o JSON. Se rechazan contextos y sintaxis
desconocidos y expansiones mayores a 4096 combinaciones. Las matrices dinámicas
se rechazan cuando `runs-on` lee `matrix.*`; si el selector no depende de la
matriz, todas sus filas conservan el mismo runner ya comprobado.
La captura Git limita el árbol a 512 archivos, 2 MiB por blob y 32 MiB totales;
rechaza symlinks y entradas que no sean archivos regulares. El hook limita la
entrada a 512 actualizaciones y 1 MiB. Sobrepasar esos límites falla de forma
cerrada, sin omitir archivos o refs.
Cuando una entrada existe en dispatch y workflow_call, se verifican ambas
declaraciones: tipos incompatibles se rechazan y una entrada string del caller
no se limita a las choices de dispatch. Las variables `vars.*` son strings y
solo se admiten en comparaciones de igualdad/desigualdad con literales: se
prueban todos los literales, el valor vacío y el resto. Nunca se devuelve la
variable como etiqueta ni se inserta libremente en texto o JSON.
Las entradas numéricas se rechazan: no se aproximan con valores string.
Los reutilizables que usan `github.event_name` en el selector se rechazan porque
ese contexto pertenece al caller. `format` y `contains` escalar requieren
argumentos string; las conversiones implícitas no comprobadas fallan de forma
cerrada. `contains` de arreglos usa igualdad de primitivas; una comparación
string/primitiva mixta también se rechaza cuando necesitaría conversión numérica.

Los reutilizables locales se recorren, con detección de ciclos. Un reutilizable
remoto debe usar un SHA de 40 caracteres y tener una copia exacta revisada en
`approved_reusables.json`, junto con su SHA-256. Su contenido también se audita;
los reutilizables locales anidados se resuelven en el repositorio/revisión
remotos, nunca contra el caller. El manifiesto central contiene la copia de
`Eternet/github/update_editorconfig.yml` obtenida por GitHub MCP en el commit
integrado `a3aff8b087edb7e8e0e86b7cdb1ba2e140d4643c`. Para aprobar otra referencia,
obtener primero su contenido autoritativo, revisar todos sus jobs y dependencias
y guardar el texto exacto y su digest en un PR de Eternet/github. Una rama mutable
o un SHA sin esa evidencia se rechazan.

El control valida la selección declarada y no demuestra SDKs disponibles,
capacidad libre, ejecución nativa ni despliegues. La verificación operativa debe
conservar además el nombre y etiquetas del runner real de cada ejecución.
