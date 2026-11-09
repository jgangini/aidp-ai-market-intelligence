# Distribución del paquete

El repositorio distribuye un paquete Python y las fuentes AIDP para Deploy Studio. La versión Python se declara en `pyproject.toml`; la versión e identidad del paquete AIDP, en `package/lab.json`. Son contratos distintos.

## Referencia de instalación

Una instalación debe fijar el commit o release y usar sus fuentes, hooks, inventario y dependencias juntos. Actualizar una rama no sustituye una release existente. Los tags y assets inmutables no se sobrescriben: una publicación de release requiere una referencia nueva.

El manifiesto `terraform/deploy-studio.json` identifica el proyecto y las rutas que necesita el runner. Deploy Studio resuelve y fija la referencia seleccionada antes de preparar Terraform y hooks. No mezclar archivos de distintas referencias ni entregar un token GitHub al agente.

## Contenido

- Fuentes Python administrativas en `src/`.
- Fuentes canónicas y descriptores AIDP en `aidp/`.
- Inventario de archivos y hashes en `package/lab.json`.
- Terraform, formulario y hooks en `terraform/`.
- Dependencias Python fijadas en `uv.lock`.

`uv build` genera wheel y distribución fuente. Una entrega de release puede añadir ZIP de fuente, manifiesto de distribución y `SHA256SUMS`; verificar commit, versiones, tamaños y hashes antes de consumirlos. No confundir una wheel construida con un agente publicado en AIDP.

## Exclusiones y procedencia

No distribuir `.oci`, `.local`, `.aidp-local`, settings reales, estados Terraform, entornos virtuales ni tokens. Los artefactos de pruebas y bitácoras de desarrollo permanecen en almacenamiento local privado; la documentación pública describe el producto y sus contratos.

La procedencia del instalador está en [provenance-installer.json](provenance-installer.json). Las rutas de origen identifican la extracción y atribución del código; no son dependencias de repositorios vecinos. Conservar licencia y atribuciones al redistribuir.
