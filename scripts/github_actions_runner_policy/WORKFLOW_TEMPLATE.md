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
guarda. Los cambios de workflows/actions y de configuraciones de control
existentes activan PR y push a la rama predeterminada; por tanto un cambio de
Actions puede generar dos validaciones, una en PR y otra tras el merge. El
disparo manual permanece disponible. Cada PR/ref cancela su ejecución anterior
de solo lectura y el job tiene cinco minutos de timeout.

La auditoría local anterior al push sigue revisando el árbol Git completo de
cada ref enviado aunque el cambio no toque un workflow. Esa comprobación
previene el caso de una rama vieja con un workflow hospedado no editado; no
consume un job de Actions por cada cambio de código.
