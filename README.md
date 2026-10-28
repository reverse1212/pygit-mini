# pygit — Mini Git en Python (proyecto de aprendizaje)

Implementación de Git desde cero en Python puro (solo librería estándar):
objetos sha1+zlib, tres (blobs, trees, commits, tags) e interoperabilidad
total con el comando `git` real.

## Cómo empezar

```bash
# con Python ≥ 3.12 directamente:
python pygit.py init          # crea la estructura .git

# o como CLI instalable:
pip install -e .
pygit init
```

## Flujo completo soportado

| Comando | Descripción |
|---|---|
| `pygit init` | Crea `.git` (objects, refs/heads, HEAD) |
| `pygit add <archivos\|.>` | Staging selectivo: crea blobs del index |
| `pygit commit -m "msg"` | Tree (anidado, formato binario de git) + commit |
| `pygit log` | Historial con orden topológico por tiempo |
| `pygit status` | Staged / unstaged / untracked |
| `pygit diff` | Diff unificado entre HEAD y el working tree |
| `pygit branch [-d <rama>]` | Lista o borra ramas |
| `pygit checkout <rama>` | Cambia/crea rama y materializa el working tree |
| `pygit merge <rama>` | 3-way merge con resolución de conflictos |
| `pygit tag [-a -m "msg"] <nombre>` | Tags ligeros y anotados (objetos tag reales) |
| `pygit rm` | Borra del disco y del index |

## Interoperabilidad con git

Cada objeto se escribe en el formato binario de git real:

```
.git/objects/<sha2>/<sha-resto>   # zlib("<tipo> <tamaño>\0" + datos)
.git/refs/heads/<rama>            # refs de ramas
.git/refs/tags/<tag>              # tags ligeros y anotados
```

Por lo tanto `git log --graph`, `git fsck`, `git show`, `git ls-tree` y
`git tag` leen los repositorios creados con `pygit` sin errores.

## Tests

```bash
pip install pytest
pytest -q
```

## Créditos

Inspirado en la serie de Ben Hoyt sobre su [pygit de 500 líneas](https://github.com/benhoyt/pygit).
