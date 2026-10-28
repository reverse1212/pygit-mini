#!/usr/bin/env python3
"""pygit: una mini implementación de Git en Python puro.

Serie de aprendizaje: cada commit añade un comando nuevo.
Fase 1: init   -> estructura .git
Fase 2: commit -> blobs, trees y commits reales (sha1 + zlib)
        log    -> sigue la cadena de padres desde refs/heads/master
"""

import argparse
import hashlib
import heapq
import os
import sys
import time
import zlib

# --- Utilidades de rutas ---

def repo_dir(repo, *path, mkdir=False):
    """Devuelve la ruta bajo .git/, creando los directorios si hace falta."""
    d = os.path.join(repo, ".git", *path)
    if mkdir and not os.path.exists(d):
        os.makedirs(d)
    return d


# --- Objetos (fase 2) ---

# Tipos de objeto
OBJ_BLOB = b"blob"
OBJ_TREE = b"tree"
OBJ_COMMIT = b"commit"

# Entradas del index (fase 2 mínima: lista de (path, sha1hex) ordenada por path)


def write_object(repo, data, obj_type):
    """Escribe un objeto en .git/objects y devuelve su sha1 hex.

    Formato: "<tipo> <tamaño>\\x00<contenido>", comprimido con zlib.
    """
    full = obj_type + b" " + str(len(data)).encode() + b"\x00" + data
    sha = hashlib.sha1(full).hexdigest()
    dir_ = repo_dir(repo, "objects", sha[:2], mkdir=True)
    path = os.path.join(dir_, sha[2:])
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(zlib.compress(full))
    return sha


def read_object(repo, sha):
    """Lee un objeto y devuelve (tipo, contenido)."""
    path = os.path.join(repo_dir(repo, "objects"), sha[:2], sha[2:])
    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())
    # "tipo tamaño\0contenido"
    nul = raw.index(b"\x00")
    obj_type, size = raw[:nul].split(b" ")
    return obj_type, raw[nul + 1:]


def find_object(repo, prefix):
    """Busca un objeto por prefijo de sha1. Devuelve el sha completo o None."""
    if len(prefix) == 40:
        return prefix
    subdir = repo_dir(repo, "objects", prefix[:2])
    if not os.path.isdir(subdir):
        return None
    matches = [prefix[:2] + f for f in os.listdir(subdir)
               if f.startswith(prefix[2:])]
    if len(matches) == 1:
        return matches[0]
    return None


def index_write(repo, entries):
    """Escribe el index de pygit como '<sha1> <path>\\n'.

    Usa .git/pygit-index para no chocar con el index binario de git real.
    """
    with open(repo_dir(repo, "pygit-index"), "w") as f:
        for path, sha in sorted(entries):
            f.write(f"{sha} {path}\n")


def index_read(repo):
    """Lee el index de pygit, devuelve lista de (path, sha)."""
    entries = []
    path = repo_dir(repo, "pygit-index")
    if not os.path.exists(path):
        return entries
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                sha, p = line.split(" ", 1)
                entries.append((p, sha))
    return entries


def index_is_dirty(repo):
    """True si hay archivos añadidos/modificados o borrados que no estén
    registrados en el index con su sha actual."""
    current = {}
    for root, _, filenames in os.walk(repo):
        if ".git" in root.split(os.sep):
            continue
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            with open(os.path.join(root, fn), "rb") as f:
                data = f.read()
            current[path] = write_object(repo, data, OBJ_BLOB)
    indexed = dict(index_read(repo))
    if set(current) != set(indexed):
        return True
    return any(current[p] != indexed[p] for p in current)


def commit(repo, message):
    """Crea blobs para los archivos del index/workdir, un tree y un commit."""
    # Blobs del working tree (autor ya apareció en write_object vía index)
    entries = {}
    for root, _, filenames in os.walk(repo):
        if ".git" in root.split(os.sep):
            continue
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            with open(os.path.join(root, fn), "rb") as f:
                sha = write_object(repo, f.read(), OBJ_BLOB)
            entries[path] = sha
    index_write(repo, entries.items())

    # Tree construido recursivamente: los subdirectorios son trees anidados
    tree_sha = build_tree(repo, entries)

    # Commit: árbol + padre (si hay) + mensaje
    head = commit_head(repo)
    parent = b"parent " + head.encode() + b"\n" if head else b""
    timestamp = int(time.time())
    tz = time.strftime("%z")
    author = "PyGit User <pygit@example.com>"
    data = (
        b"tree " + tree_sha.encode() + b"\n"
        + parent
        + b"author " + author.encode() + b" " + str(timestamp).encode() + b" " + tz.encode() + b"\n"
        + b"committer " + author.encode() + b" " + str(timestamp).encode() + b" " + tz.encode() + b"\n\n"
        + message.encode() + b"\n"
    )
    sha = write_object(repo, data, OBJ_COMMIT)

    # Avanza la ref de la rama actual
    with open(repo_dir(repo, "HEAD")) as f:
        ref = f.read().strip().split(" ")[-1]
    ref_path = repo_dir(repo, *ref.split("/"))
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    with open(ref_path, "w", newline="\n") as f:
        f.write(sha + "\n")
    print(f"[{ref.split('/')[-1]} {sha[:7]}] {message}")


def commit_head(repo):
    """Devuelve el sha del último commit de la rama actual, o None."""
    with open(repo_dir(repo, "HEAD")) as f:
        ref = f.read().strip().split(" ")[-1]
    ref_path = repo_dir(repo, *ref.split("/"))
    if not os.path.exists(ref_path):
        return None
    with open(ref_path) as f:
        return f.read().strip()


# --- Rama actual (fase 3) ---

def read_ref(repo):
    """Devuelve el nombre de la rama actual (p.ej. 'master')."""
    with open(repo_dir(repo, "HEAD")) as f:
        ref = f.read().strip().split(" ")[-1]
    return ref[len("refs/heads/"):]


def set_head_branch(repo, branch):
    """Hace que HEAD apunte a refs/heads/<branch>."""
    with open(repo_dir(repo, "HEAD"), "w", newline="\n") as f:
        f.write(f"ref: refs/heads/{branch}\n")


def branch_write(repo, branch, sha):
    """Crea o actualiza refs/heads/<branch>."""
    path = repo_dir(repo, "refs", "heads", branch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n") as f:
        f.write(sha + "\n")


# --- Trees, recursivos (fase 3) ---

def build_tree(repo, entries):
    """Construye trees anidados a partir de {path: sha} y devuelve el tree raíz."""
    dirs = {}       # nombre -> subconjunto de {path relativo: sha}
    files = []
    for path, sha in sorted(entries.items()):
        head, _, tail = path.partition("/")
        if tail:
            dirs.setdefault(head, {})[tail] = sha
        else:
            files.append((head, sha))
    items = [
        (b"40000", mi_dir, bytes.fromhex(build_tree(repo, sub)))
        for mi_dir, sub in sorted(dirs.items())
    ] + [(b"100644", name, bytes.fromhex(sha)) for name, sha in sorted(files)]
    items.sort(key=lambda item: item[1])
    data = b"".join(
        mode + b" " + name.encode() + b"\x00" + sha for mode, name, sha in items
    )
    return write_object(repo, data, OBJ_TREE)


def tree_read(repo, sha):
    """Parsea un tree binario: lista de (mode, type, item_sha, name)."""
    items = []
    try:
        obj_type, data = read_object(repo, sha)
    except FileNotFoundError:
        raise Exception(f"Árbol {sha} no encontrado")
    pos = 0
    while pos < len(data):
        nul = data.index(b"\x00", pos)
        mode_name = data[pos:nul].decode()
        mode, name = mode_name.split(" ")
        item_sha = data[nul + 1:nul + 21].hex()
        obj_type = OBJ_TREE if mode == "40000" else OBJ_BLOB
        items.append((mode, obj_type, item_sha, name))
        pos = nul + 21
    return items


def get_index_tree(repo):
    """Devuelve {path: sha} con todos los archivos del tree del commit HEAD."""
    index = {}
    if not commit_head(repo):
        return index
    _, data = read_object(repo, commit_head(repo))
    tree_sha = data.split(b"\n")[0].split(b" ")[1].decode()

    def walk(sha, prefix):
        for mode, obj_type, item_sha, name in tree_read(repo, sha):
            path = f"{prefix}{name}"
            if obj_type == OBJ_TREE:
                walk(item_sha, path + "/")
            else:
                index[path] = item_sha

    walk(tree_sha, "")
    return index


# --- Status (fase 3) ---

def get_status(repo):
    """Compara HEAD <- pygit-index <- working tree.

    Devuelve (add, delete, modified):
    - add:      en index/HEAD pero no en disco (o viceversa)
    - delete:   en index pero borrado en disco y sin cambios staged
    - modified: sha distinto entre disco y lo registrado
    """
    # sha del disco por archivo
    disk = {}
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            with open(os.path.join(root, fn), "rb") as f:
                disk[path] = write_object(repo, f.read(), OBJ_BLOB)
    indexed = dict(index_read(repo))
    head_tree = get_index_tree(repo)

    add = [p for p in disk if p not in indexed and p not in head_tree]
    delete = [p for p in indexed if p not in disk and indexed[p] == head_tree.get(p)]
    modified = [p for p in disk if p in indexed and indexed[p] != disk[p]]
    return add, delete, modified


# --- Checkout (fase 3) ---

def checkout(repo, target):
    """Cambia de rama (creándola si no existe) y reconstruye el working tree."""
    branch_path = repo_dir(repo, "refs", "heads", target)
    if not os.path.exists(branch_path):
        head = commit_head(repo)
        if not head:
            raise Exception("No hay commits a los que hacer checkout")
        branch_write(repo, target, head)
        print(f"Cambiado a nueva rama '{target}'")
    else:
        print(f"Cambiado a rama '{target}'")

    set_head_branch(repo, target)
    head = commit_head(repo)
    assert head, "Rama sin commit"

    # Recolecta y materializa los archivos de la rama destino
    obj_type, data = read_object(repo, head)
    tree_sha = data.split(b"\n")[0].split(b" ")[1].decode()
    target_files = set()

    def walk(sha, prefix, out):
        for mode, obj_type, item_sha, name in tree_read(repo, sha):
            path = f"{prefix}{name}"
            if obj_type == OBJ_TREE:
                os.makedirs(os.path.join(repo, prefix, name), exist_ok=True)
                walk(item_sha, path + "/", out)
            else:
                out.add(path)
                obj_type_b, blob = read_object(repo, item_sha)
                with open(os.path.join(repo, prefix, name), "wb") as f:
                    f.write(blob)

    walk(tree_sha, "", target_files)

    # Borra del working tree los archivos que no pertenecen a la rama destino
    removed = []
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            if path not in target_files:
                os.remove(os.path.join(root, fn))
                removed.append(path)
    for name in removed:
        print(f"Borrado del working tree: {name}")




# --- Log (fase 2) ---

def log(repo):
    """Historial de commits a partir de la rama actual (simplemente ascii)."""
    head = commit_head(repo)
    if not head:
        print("No hay commits todavía.")
        return
    # Ordena por tiempo (heap) y muestra la línea de cada commit
    shas = []
    seen = set()
    head_obj_type, head_data = read_object(repo, head)
    timestamp = commit_parse_timestamp(head_data)
    heapq.heappush(shas, (timestamp, head))
    while shas:
        timestamp, sha = heapq.heappop(shas)
        if sha in seen:
            continue
        seen.add(sha)
        obj_type, data = read_object(repo, sha)
        lines = data.decode(errors="replace").splitlines()
        tree = next(l[5:] for l in lines if l.startswith("tree "))
        parent = next((l[7:] for l in lines if l.startswith("parent ")), None)
        if parent and parent not in seen:
            p_type, p_data = read_object(repo, parent)
            heapq.heappush(shas, (commit_parse_timestamp(p_data), parent))
        message = lines[lines.index("") + 1:]
        print(f"{sha[:7]} (tree {tree[:7]}) {message[0] if message else ''}")


def commit_parse_timestamp(data):
    """Extrae el timestamp de la línea 'author ... <ts> <tz>'."""
    text = data.decode(errors="replace")
    for line in text.splitlines():
        if line.startswith("author "):
            return int(line.rsplit(" ", 2)[-2])
    return 0


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
    with open(repo_dir(repo, "HEAD"), "w", newline="\n") as f:
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

    p_commit = sub.add_parser("commit", help="Crea un commit de pygit")
    p_commit.add_argument("-m", "--message", required=True, help="Mensaje del commit")

    sub.add_parser("log", help="Muestra el historial de commits")

    sub.add_parser("status", help="Archivos añadidos, borrados y modificados")

    p_checkout = sub.add_parser("checkout", help="Cambia de rama (la crea si no existe)")
    p_checkout.add_argument("branch", help="Rama destino")

    args = parser.parse_args(argv)

    if args.command == "init":
        repo_init(args.repo)
    elif args.command == "commit":
        commit(".", args.message)
    elif args.command == "log":
        log(".")
    elif args.command == "status":
        add, delete, modified = get_status(".")
        for name in add:
            print(f"added:   {name}")
        for name in delete:
            print(f"deleted: {name}")
        for name in modified:
            print(f"modified: {name}")
        if not (add or delete or modified):
            print("Sin cambios.")
    elif args.command == "checkout":
        checkout(".", args.branch)
    else:
        parser.error(f"Comando desconocido: {args.command}")


if __name__ == "__main__":
    main()
