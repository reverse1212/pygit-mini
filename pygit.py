#!/usr/bin/env python3
"""pygit: una mini implementación de Git en Python puro.

Serie de aprendizaje: cada commit añade un comando nuevo.
Fase 1: init (crear la estructura de un repositorio .git).
"""

import argparse
import os
import sys

# --- Utilidades de rutas ---

def repo_dir(repo, *path, mkdir=False):
    """Devuelve la ruta bajo .git/, creando los directorios si hace falta."""
    d = os.path.join(repo, ".git", *path)
    if mkdir and not os.path.exists(d):
        os.makedirs(d)
    return d


# --- Comandos ---

def repo_init(repo=".", workdir_init=True):
    """Crea la estructura mínima de un repositorio pygit en repo/.git.

    Devuelve True si se creó, False si ya existía.
    """
    gitdir = os.path.join(repo, ".git")
    if os.path.exists(gitdir):
        print(f"Ya existe un repositorio pygit en {os.path.abspath(gitdir)}")
        return False

    # Directorios de objetos y refs (como git real)
    repo_dir(repo, "objects", mkdir=True)
    repo_dir(repo, "refs", "heads", mkdir=True)

    # HEAD apunta a la rama master
    with open(repo_dir(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")

    # Descripción (informativo, igual que git)
    with open(repo_dir(repo, "description"), "w") as f:
        f.write("Repositorio pygit de aprendizaje\n")

    print(f"Repositorio pygit inicializado en {os.path.abspath(gitdir)}")
    return True


def main(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser(prog="pygit", description="Mini git en Python")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Inicializa un repositorio pygit")
    p_init.add_argument("repo", nargs="?", default=".", help="Directorio (por defecto: .)")

    args = parser.parse_args(argv)

    if args.command == "init":
        repo_init(args.repo)
    else:
        parser.error(f"Comando desconocido: {args.command}")


if __name__ == "__main__":
    main()
