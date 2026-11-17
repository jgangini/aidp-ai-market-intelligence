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

La wheel y la distribución fuente incorporan únicamente los assets AIDP declarados y verificados por el paquete, además de `package/lab.json`. Los archivos privados o antiguos presentes en `aidp/` que no pertenezcan al inventario no se incluyen por recorrer el directorio. Se comprueban de nuevo sus hashes al leer y se conserva la misma metadata validada. La wheel sustituye íntegramente su prefijo `_assets/`; la distribución fuente usa un directorio de release nuevo y registra sólo las fuentes efectivamente distribuidas.

El build comprueba que el validador importado y la raíz efectiva de assets pertenezcan al checkout seleccionado. Una instalación previa en caché o una copia `_assets` ajena no pueden sustituir las fuentes actuales silenciosamente.

## Exclusiones y procedencia

No distribuir `.oci`, `.local`, `.aidp-local`, settings reales, estados Terraform, entornos virtuales ni tokens. Los artefactos de pruebas y bitácoras de desarrollo permanecen en almacenamiento local privado; la documentación pública describe el producto y sus contratos.

El lector del paquete y el comprobador de fuentes comparten la clasificación de rutas privadas. Se rechazan segmentos privados y nombres `.env*` sin distinguir mayúsculas, archivos `.tfvars` y variantes `*.tfstate*`, incluidos respaldos. La publicación comprueba también el destino resuelto: un alias de nombre público no puede apuntar a un archivo privado. Los enlaces internos a fuentes públicas siguen permitidos por el lector del paquete. `terraform/example.tfvars.json` sigue siendo un ejemplo público permitido. Las instrucciones públicas `.codex/AGENTS.md` y `.codex/improvement-log.md` pueden versionarse en el repositorio, pero no formar parte de las fuentes publicadas del agente.

El manifiesto exige destinos únicos dentro del artefacto y una única fuente canónica por archivo editable. Un archivo generado declara su cierre no vacío en `assembly_sources`; su campo `source` puede estar ausente o ser `null`. La validación y el inventario de hashes interpretan ambos casos de la misma forma. Se comprueban también las definiciones de recursos y los bytes de cada fuente antes de construir el paquete.

La procedencia del instalador está en [provenance-installer.json](provenance-installer.json). Las rutas de origen identifican la extracción y atribución del código; no son dependencias de repositorios vecinos. Conservar licencia y atribuciones al redistribuir.

## Licencia

La distribución Python declara la licencia MIT y utiliza este README como descripción del proyecto. La wheel y la distribución fuente contienen el texto de `LICENSE`; la distribución fuente también incluye `README.md`. Los derechos y condiciones de las dependencias y de los servicios consultados son independientes de la licencia de este repositorio.
