# Un único contrato para los disparadores

`render_workflow.py` genera el stub completo de todos los consumidores. No
copiar bloques YAML ni agregar un job que filtre después de asignar un runner.

```powershell
python scripts/github_actions_runner_policy/render_workflow.py --root C:/repos/Netmap --default-branch main --policy-ref COMMIT_INTEGRADO --write
```

Reemplazar el pin por un SHA integrado de Eternet/github. `--check` verifica la
igualdad exacta sin escribir. El propio repositorio usa `--central`, que agrega
el código y pruebas del control a sus paths y consume la acción local.

Un cambio normal de C#, CSS o documentación no inicia ningún job de esta
guarda. Los cambios de workflows/actions activan PR y push a la rama
predeterminada; por tanto un cambio de
Actions puede generar dos validaciones, una en PR y otra tras el merge. El
disparo manual permanece disponible. Cada PR/ref cancela su ejecución anterior
de solo lectura y el job tiene cinco minutos de timeout.

La auditoría local anterior al push sigue revisando el árbol Git completo de
cada ref enviado aunque el cambio no toque un workflow. Esa comprobación
previene el caso de una rama vieja con un workflow hospedado no editado; no
consume un job de Actions por cada cambio de código.

El checker lee todos los YAML de `.github/workflows`, incluidas matrices
literales y reutilizables locales de ese árbol. Las matrices dinámicas que
afectan al selector se rechazan sin leer archivos generados fuera del árbol.
El manifiesto `approved_reusables.json` se lee desde la acción central y queda
cubierto por `scripts/github_actions_runner_policy/**` en su self-check.
No hay configuración por consumidor que el checker lea: `actionlint.yaml`
pertenece a otra herramienta y no dispara esta guarda.

## Checks obligatorios

Un workflow omitido por `paths` no emite un check para un cambio normal. Por
eso su contexto `runner-policy` no debe marcarse required en una regla que
exija resultado para cada PR. Antes de adoptar filtros se debe consultar el
estado efectivo de la rama y los contextos clásicos/rulesets disponibles;
un HTTP403 de permisos o plan no demuestra ausencia de reglas.

La auditoría del 2026-11-02 leyó las 45 ramas predeterminadas por REST: todas
informaron `protected: false`, `required_status_checks.enforcement_level: off`
y listas `contexts`/`checks` vacías. En el repositorio público se pudieron leer
además las reglas activas, vacías. En los 44 privados, la consulta detallada de
rulesets devolvió HTTP403 por disponibilidad del plan; se conserva esa
limitación sin atribuirle una verificación administrativa completa. No se
cambiaron reglas ni se agregó un job para cada cambio de código.
