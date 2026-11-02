# Control central y comprobación antes de publicar

Los repositorios de producto conservan sus workflows existentes. No se añade
un YAML o job de política por consumidor. El self-check único de Eternet/github
solo se activa ante cambios de workflows/actions o del paquete del control;
PR y push a main usan los mismos paths, con cancelación por PR/ref y timeout
de cinco minutos. Un cambio de Actions puede provocar un check en PR y otro
tras el merge. Código C#, CSS y documentación ordinaria no lo disparan.

El hook previo al push audita todos los commits/refs que Git va a enviar. La
CLI también permite verificar el ref exacto de un dispatch o re-run histórico.
No usa el editor, el diff o un main diferente como sustituto del árbol fuente.

## Entradas efectivamente leídas

El checker lee los YAML de `.github/workflows`, incluidas sus matrices
literales y reutilizables locales. Una matriz dinámica que afecta al selector
se rechaza si no puede probarse su procedencia; no lee un archivo JSON
generado fuera de ese árbol para aceptar valores desconocidos.

`approved_reusables.json` pertenece al paquete central. Su modificación queda
cubierta por `scripts/github_actions_runner_policy/**`. No se lee una
configuración por consumidor ni `actionlint.yaml`, que pertenece a otra
herramienta. No se incluyen esos archivos en filtros que no los consumen.

## Checks obligatorios

Un workflow omitido por `paths` no emite un check para un cambio normal. Su
contexto no debe marcarse required para cada PR; no se agrega otro job que
asigne un runner en cada cambio de código para solventarlo.

La lectura REST del 2026-11-02 de las 45 ramas predeterminadas mostró
`protected: false`, `required_status_checks.enforcement_level: off` y listas
`contexts`/`checks` vacías. En Eternet/github también se pudieron leer reglas
activas vacías. La consulta detallada de rulesets de los 44 privados devolvió
HTTP403 por disponibilidad del plan; no se tomó ese error como prueba de
ausencia de reglas. No se alteraron reglas ni protecciones de ramas.

## Límites

La instalación local no intercepta publicaciones desde otros equipos, la web
o APIs. Las instrucciones de los asistentes exigen auditar igualmente el árbol
completo antes de usar esas vías. No se afirma una prohibición global del
servicio ni se activa un bloqueo organizacional que pueda afectar runners
correctos o requiera cambiar su selección por grupos.
