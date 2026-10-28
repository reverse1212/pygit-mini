#!/usr/bin/env python3
"""pygit: una mini implementación de Git en Python puro.

Serie de aprendizaje: cada commit añade un comando nuevo.
Fase 1: init   -> estructura .git
Fase 2: commit -> blobs, trees y commits reales (sha1 + zlib)
        log    -> sigue la cadena de padres desde refs/heads/master
"""

import argparse
import difflib
import hashlib
import heapq
import json
import os
import shutil
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


def commit(repo, message):
    """Crea el tree desde el index (con 'pygit add' selectivo) y un commit."""
    entries = dict(index_read(repo))
    head = commit_head(repo)
    if not entries:
        # aborta solo si de verdad no hay nada que commitear
        if not head or commit_index(repo, head) == {}:
            print("No hay nada en el index (usa 'pygit add'); aborta commit")
            sys.exit(1)

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


# --- Status (fase 3, reescrita en fase 7) ---

def get_status(repo):
    """Compara HEAD <- index (staged) <- working tree (unstaged).

    Devuelve (staged, unstaged) donde cada uno es (add, delete, modified).
    """
    disk = {}
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            with open(os.path.join(root, fn), "rb") as f:
                disk[path] = write_object(repo, f.read(), OBJ_BLOB)
    index = dict(index_read(repo))
    head_tree = commit_index(repo, commit_head(repo))

    staged = (
        [p for p in index if p not in head_tree],                       # added
        [p for p in head_tree if p not in index],                       # deleted
        [p for p in index if p in head_tree and index[p] != head_tree[p]],  # modified
    )
    unstaged = (
        [p for p in disk if p not in index],                            # added
        [p for p in index if p not in disk],                            # deleted
        [p for p in disk if p in index and disk[p] != index[p]],        # modified
    )
    return staged, unstaged


# --- Add / rm, staging selectivo (fase 7) ---

def _paths_from_args(repo, paths):
    """Expande argumentos ('.', archivos o directorios) a rutas relativas."""
    result = []
    for arg in paths:
        norm = os.path.relpath(arg, repo).replace(os.sep, "/")
        disk = os.path.join(repo, norm.replace("/", os.sep))
        if os.path.isdir(disk) and not os.path.isfile(disk):
            for root, dirs, filenames in os.walk(disk):
                dirs[:] = [d for d in dirs if d != ".git"]
                root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
                for fn in filenames:
                    result.append(fn if root_rel == "." else f"{root_rel}/{fn}")
        elif norm != ".":
            result.append(norm)
        else:
            result.append(arg.replace(os.sep, "/"))
    return result


def add(repo, paths):
    """Añade archivos al index (crea el blob de cada uno)."""
    entries = dict(index_read(repo))
    for path in _paths_from_args(repo, paths):
        disk = os.path.join(repo, path.replace("/", os.sep))
        if not os.path.exists(disk):
            print(f"ERROR: no existe '{path}' (usa 'rm' para borrar del index)")
            sys.exit(1)
        with open(disk, "rb") as f:
            data = f.read()
        entries[path] = write_object(repo, data, OBJ_BLOB)
        print(f"Añadido al index: {path}")
    index_write(repo, entries.items())


def remove(repo, paths):
    """Borra archivos del working tree y del index (como git rm)."""
    entries = dict(index_read(repo))
    for path in _paths_from_args(repo, paths):
        disk = os.path.join(repo, path.replace("/", os.sep))
        if path in entries:
            del entries[path]
            print(f"Borrado del index: {path}")
        else:
            print(f"AVISO: '{path}' no estaba en el index")
        if os.path.exists(disk):
            os.remove(disk)
    index_write(repo, entries.items())


# --- Checkout (fase 3; materialize extraída en fase 11) ---

def materialize(repo, commit_sha):
    """Escribe el tree de un commit en el working tree, borra lo sobrante
    y reinicia el index (usado por checkout y pull)."""
    obj_type, data = read_object(repo, commit_sha)
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

    # borra del working tree los archivos que no pertenecen al target
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            if path not in target_files:
                os.remove(os.path.join(root, fn))

    # el index se reinicia al contenido exacto del commit
    index_write(repo, commit_index(repo, commit_sha).items())


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
    materialize(repo, head)




# --- Commit: helpers (fase 4) ---

def commit_data_parse(repo, sha):
    """Devuelve (tree_sha, [padres], mensaje) de un commit."""
    obj_type, data = read_object(repo, sha)
    text = data.decode(errors="replace")
    lines = text.splitlines()
    blank = lines.index("")
    tree_sha = ""
    parents = []
    for line in lines[:blank]:
        field, _, value = line.partition(" ")
        if field == "tree":
            tree_sha = value
        elif field == "parent":
            parents.append(value)
    return tree_sha, parents, lines[blank + 1:]


def commit_index(repo, head):
    """Devuelve {path: sha} con todos los archivos del tree de un commit."""
    index = {}
    if not head:
        return index
    tree_sha = commit_data_parse(repo, head)[0]

    def walk(sha, prefix):
        for mode, obj_type, item_sha, name in tree_read(repo, sha):
            path = f"{prefix}{name}"
            if obj_type == OBJ_TREE:
                walk(item_sha, path + "/")
            else:
                index[path] = item_sha

    walk(tree_sha, "")
    return index


def merge_base(repo, a, b):
    """Devuelve el ancestor común más reciente de dos commits (o None)."""
    # Todos los ancestros de 'a'
    ancestors_a = set()
    stack = [a]
    while stack:
        sha = stack.pop()
        if sha in ancestors_a:
            continue
        ancestors_a.add(sha)
        stack.extend(commit_data_parse(repo, sha)[1])

    # Recorre ancestros de 'b' hasta tocar el conjunto de 'a'
    seen = set()
    stack = [b]
    while stack:
        sha = stack.pop()
        if sha in seen:
            continue
        seen.add(sha)
        if sha in ancestors_a:
            return sha
        stack.extend(commit_data_parse(repo, sha)[1])
    return None


# --- Merge (fase 4; acepta sha/remoto desde fase 11) ---

def merge(repo, branch, name=None):
    """Merge de <branch> (rama, refs/remotes/x/y o sha) en la rama actual."""
    our_branch = read_ref(repo)
    head = commit_head(repo)
    branch_ref = repo_dir(repo, "refs", "heads", branch)
    if os.path.exists(branch_ref):
        with open(branch_ref) as f:
            their_head = f.read().strip()
    else:
        # puede ser un sha (pull desde refs/remotes) — el objeto ya lo tenemos
        their_head = find_object(repo, branch)
    assert their_head, f"Rama o sha '{branch}' sin commits"

    # El ancestor común
    ancestor = merge_base(repo, head, their_head)
    ancestor = commit_index(repo, ancestor)
    ours = commit_index(repo, head)
    theirs = commit_index(repo, their_head)

    changed = False
    conflicted_paths = []
    for path in set(ancestor) | set(ours) | set(theirs):
        ash = ancestor.get(path)
        bsh = ours.get(path)
        csh = theirs.get(path)
        # igual en ambas ramas: no hay nada que hacer
        if bsh == csh:
            continue
        elif ash == bsh:               # solo cambia en la otra rama: coger c
            changed = True
            if csh is None:
                del ours[path]
            else:
                ours[path] = csh
        elif ash == csh:               # solo cambia en nuestra rama: vale b
            continue
        else:
            base_data = ours_data = their_data = None
            if ash is not None:
                base_data = read_object(repo, ash)[1]
            if bsh is not None:
                ours_data = read_object(repo, bsh)[1]
            if csh is not None:
                their_data = read_object(repo, csh)[1]
            merged, conflicted = merge_content(
                base_data or b"", ours_data or b"", their_data or b"",
                label=(name or (branch[:7] if len(branch) == 40 else branch)),
            )
            # sustituye la entrada por el texto fusionado (con marcadores si hay)
            ours[path] = write_object(repo, merged, OBJ_BLOB)
            if conflicted:
                conflicted_paths.append(path)
            else:
                print(f"Auto-merged {path}")
    # Actualiza el working tree con el resultado (archivos de 'ours')
    for path, sha in ours.items():
        dest = os.path.join(repo, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(read_object(repo, sha)[1])
    # y borra del disco los archivos que el merge eliminó
    on_disk = []
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        on_disk += [fn if root_rel == "." else f"{root_rel}/{fn}" for fn in filenames]
    for path in set(on_disk) - set(ours):
        os.remove(os.path.join(repo, path.replace("/", os.sep)))

    # index = files (staged)
    index_write(repo, ours.items())

    # Si hay conflictos, dejamos los marcadores en el working tree para que el
    # usuario los resuelva y después haga `pygit commit` manual del merge
    if conflicted_paths:
        for path in conflicted_paths:
            print(f"CONFLICTO en {path}: edita el archivo, ejecuta 'pygit add {path}'")
            print("y después 'pygit commit -m \"merge tal\"' para completarlo")
        print("ERROR: Conflictos sin resolver; no se crea el commit de merge")
        sys.exit(1)

    timestamp = int(time.time())
    tz = time.strftime("%z")
    author = "PyGit User <pygit@example.com>"
    label_branch = branch if len(branch) != 40 else branch[:7]
    message = f"Merged branch '{label_branch}' into {our_branch}"
    data = (
        b"tree " + build_tree(repo, ours).encode() + b"\n"
        + b"parent " + head.encode() + b"\n"
        + b"parent " + their_head.encode() + b"\n"
        + b"author " + author.encode() + b" " + str(timestamp).encode() + b" " + tz.encode() + b"\n"
        + b"committer " + author.encode() + b" " + str(timestamp).encode() + b" " + tz.encode() + b"\n\n"
        + message.encode() + b"\n"
    )
    sha = write_object(repo, data, OBJ_COMMIT)
    branch_write(repo, our_branch, sha)
    print(f"[{our_branch} {sha[:7]}] {message}")




# --- Tags (fase 8) ---

def tag_annotate(repo, name, message):
    """Crea un tag anotado: objeto 'tag' real en .git/objects y en refs/tags."""
    head = commit_head(repo)
    if not head:
        print("ERROR: no hay commits para etiquetar")
        sys.exit(1)
    tag_dir = repo_dir(repo, "refs", "tags")
    os.makedirs(tag_dir, exist_ok=True)
    path = os.path.join(tag_dir, name)
    if os.path.exists(path):
        print(f"ERROR: el tag '{name}' ya existe")
        sys.exit(1)
    timestamp = int(time.time())
    tz = time.strftime("%z")
    author = "PyGit User <pygit@example.com>"
    data = (
        b"object " + head.encode() + b"\n"
        + b"type commit\n"
        + b"tag " + name.encode() + b"\n"
        + b"tagger " + author.encode() + b" " + str(timestamp).encode() + b" " + tz.encode() + b"\n\n"
        + message.encode() + b"\n"
    )
    sha = write_object(repo, data, b"tag")
    with open(path, "w", newline="\n") as f:
        f.write(sha + "\n")
    print(f"Tag anotado '{name}' -> {sha[:7]} (commit {head[:7]})")


def tag_lightweight(repo, name, target=None):
    """Crea un tag ligero: la ref apunta directamente al commit (o sha dado)."""
    if target:
        sha = None
        # resuelve el argumento: rama o prefijo de sha
        branch = repo_dir(repo, "refs", "heads", target)
        if os.path.exists(branch):
            with open(branch) as f:
                sha = f.read().strip()
        if not sha:
            sha = find_object(repo, target)
        if not sha:
            print(f"ERROR: no encuentro '{target}' ni como rama ni como sha")
            sys.exit(1)
    else:
        sha = commit_head(repo)
        if not sha:
            print("ERROR: no hay commits para etiquetar")
            sys.exit(1)
    tag_dir = repo_dir(repo, "refs", "tags")
    os.makedirs(tag_dir, exist_ok=True)
    with open(os.path.join(tag_dir, name), "w", newline="\n") as f:
        f.write(sha + "\n")
    print(f"Tag ligero '{name}' -> {sha[:7]}")


def tag_list(repo):
    """Lista los tags; los anotados muestran el commit etiquetado."""
    tags_dir = repo_dir(repo, "refs", "tags")
    if not os.path.isdir(tags_dir):
        return
    for name in sorted(os.listdir(tags_dir)):
        with open(os.path.join(tags_dir, name)) as f:
            sha = f.read().strip()
        try:
            obj_type, data = read_object(repo, sha)
        except FileNotFoundError:
            obj_type = "?"
        if obj_type == b"tag":
            commit_sha = next(
                line.split(" ")[1] for line in data.decode().splitlines()
                if line.startswith("object ")
            )
            print(f"{name} (anotado) <== commit {commit_sha[:7]}")
        else:
            print(f"{name} -> {sha[:7]}")


def tag_delete(repo, name):
    """Borra la ref refs/tags/<name>."""
    path = repo_dir(repo, "refs", "tags", name)
    if not os.path.exists(path):
        print(f"ERROR: el tag '{name}' no existe")
        sys.exit(1)
    os.remove(path)
    print(f"Tag '{name}' borrado")


# --- Clone local (fase 9) ---

def clone(source, target):
    """Clona un repositorio local pygit/git a un directorio nuevo.

    Copia los objetos, refs y HEAD; materializa el working tree y el index.
    """
    src_git = os.path.join(source, ".git")
    if not os.path.isdir(src_git):
        print(f"ERROR: '{source}' no es un repositorio pygit/git")
        sys.exit(1)
    if os.path.exists(target):
        print(f"ERROR: el directorio destino '{target}' ya existe")
        sys.exit(1)

    repo_init(target)
    dst_objects = repo_dir(target, "objects")

    # Copia el árbol completo de objetos preservando la estructura sha-prefix
    for root, dirs, files in os.walk(os.path.join(src_git, "objects")):
        rel = os.path.relpath(root, os.path.join(src_git, "objects"))
        for fn in files:
            dst_dir = os.path.join(dst_objects, rel)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(os.path.join(root, fn), os.path.join(dst_dir, fn))

    # Ramas y tags desde las refs de origen
    imported = 0
    for kind in ("heads", "tags"):
        src_refs = os.path.join(src_git, "refs", kind)
        if os.path.isdir(src_refs):
            dst_refs = repo_dir(target, "refs", kind)
            os.makedirs(dst_refs, exist_ok=True)
            for root, dirs, files in os.walk(src_refs):
                rel = os.path.relpath(root, src_refs)
                for fn in files:
                    name = fn if rel == "." else f"{rel}\\{fn}".replace("\\x00", "/")
                    name = (fn if rel == "." else os.path.join(rel, fn))
                    dst = os.path.join(dst_refs, name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(os.path.join(root, fn), dst)
                    imported += 1

    # HEAD: usa la rama por defecto del origen
    with open(os.path.join(src_git, "HEAD")) as f:
        head_ref = f.read().strip()
    with open(repo_dir(target, "HEAD"), "w", newline="\n") as f:
        f.write(head_ref + "\n")

    # Materializa el working tree y el index de la rama por defecto
    branch = head_ref.split("refs/heads/", 1)[-1]
    head_sha_file = repo_dir(target, "refs", "heads", branch)
    if os.path.exists(head_sha_file):
        checkout(target, branch)
    print(f"Clonado {source} -> {target} ({imported} refs)")


# --- Merge de líneas, 3-way real (fase 5) ---

def sync_regions(base, ours, theirs):
    """Regiones del base en las que ours y theirs alinean.

    Devuelve (z0, z1, a0, a1, b0, b1): triple rango de líneas alineado.
    """
    am = difflib.SequenceMatcher(None, base, ours).get_matching_blocks()
    bm = difflib.SequenceMatcher(None, base, theirs).get_matching_blocks()

    def intersect(ra, rb):
        lo, hi = max(ra[0], rb[0]), min(ra[1], rb[1])
        return (lo, hi) if lo < hi else None

    regions = []
    ia = ib = 0
    while ia < len(am) and ib < len(bm):
        ab, ao, alen = am[ia]
        bb, bo, blen = bm[ib]
        overlap = intersect((ab, ab + alen), (bb, bb + blen))
        if overlap:
            z0, z1 = overlap
            length = z1 - z0
            a0 = ao + (z0 - ab)
            b0 = bo + (z0 - bb)
            regions.append((z0, z1, a0, a0 + length, b0, b0 + length))
        if ab + alen < bb + blen:
            ia += 1
        else:
            ib += 1
    return regions


def merge_content(base_data, ours_data, theirs_data, label):
    """Merge de líneas con marcadores de conflicto.

    Devuelve (bytes_resultado, conflicted: bool).
    """
    if base_data == ours_data:
        return theirs_data, False
    if base_data == theirs_data or ours_data == theirs_data:
        return ours_data, False

    # splitlines(keepends=True) sobre bytes, evitando la última línea suelta
    base = base_data.splitlines(keepends=True)
    ours = ours_data.splitlines(keepends=True)
    theirs = theirs_data.splitlines(keepends=True)
    marker_our = b"<<<<<<< HEAD\n"
    marker_sep = b"=======\n"
    marker_their = f">>>>>>> {label}\n".encode()

    regions = sync_regions(base, ours, theirs)
    result = []
    conflicted = False
    iz = ia = ib = 0

    def emit(z0, a0, b0):
        """Fusión de las líneas previas a la siguiente región estable."""
        nonlocal iz, ia, ib, conflicted
        base_lines = base[iz:z0]
        our_lines = ours[ia:a0]
        their_lines = theirs[ib:b0]
        if our_lines == their_lines:      # cambio idéntico en ambas ramas
            result.extend(our_lines)
        elif base_lines == our_lines:     # solo cambió la otra rama
            result.extend(their_lines)
        elif base_lines == their_lines:   # solo cambió la nuestra
            result.extend(our_lines)
        else:
            conflicted = True
            result.append(marker_our)
            result.extend(our_lines)
            result.append(marker_sep)
            result.extend(their_lines)
            result.append(marker_their)
        iz, ia, ib = z0, a0, b0

    for z0, z1, a0, a1, b0, b1 in regions:
        emit(z0, a0, b0)
        iz, ia, ib = z1, a1, b1
        result.extend(base[z0:z1])
    # colas restantes al final del archivo
    emit(len(base), len(ours), len(theirs))

    return b"".join(result), conflicted


# --- Push / fetch (fase 10) ---

def _copy_objects(src_git, dst_objects):
    """Copia a dst_objects los objetos de src_git que aún no existen all."""
    src_objects = os.path.join(src_git, "objects")
    copied = 0
    if not os.path.isdir(src_objects):
        return copied
    for prefix in os.listdir(src_objects):
        prefix_dir = os.path.join(src_objects, prefix)
        if len(prefix) != 2 or not os.path.isdir(prefix_dir):
            continue
        for leftover in os.listdir(prefix_dir):
            src_path = os.path.join(prefix_dir, leftover)
            dst_dir = os.path.join(dst_objects, prefix)
            dst_path = os.path.join(dst_dir, leftover)
            if not os.path.exists(dst_path):
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                copied += 1
    return copied


def _remote_name(repo, remote):
    """Resuelve el nombre corto del remoto para refs/remotes (basename)."""
    return os.path.basename(os.path.abspath(remote.rstrip("\\/")))


def fetch(repo, remote):
    """Copia objetos y ramas de <remote> a refs/remotes/<nombre>."""
    src_git = os.path.join(remote, ".git")
    if not os.path.isdir(src_git):
        print(f"ERROR: '{remote}' no es un repositorio pygit/git")
        sys.exit(1)
    name = _remote_name(repo, remote)

    copied = _copy_objects(src_git, repo_dir(repo, "objects"))

    heads = os.path.join(src_git, "refs", "heads")
    if os.path.isdir(heads):
        for root, dirs, files in os.walk(heads):
            rel = os.path.relpath(root, heads)
            for fn in files:
                name_rel = fn if rel == "." else os.path.join(rel, fn)
                dst = repo_dir(repo, "refs", "remotes", name, name_rel.replace(os.sep, "/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(root, fn), dst)
                with open(os.path.join(root, fn)) as f:
                    sha = f.read().strip()
                print(f"{name}/{name_rel} -> {sha[:7]}")
    print(f"fetch: {copied} objetos nuevos de '{name}'")


def push(repo, remote, branch=None):
    """Empuja <branch> (o la actual) a <remote>, solo fast-forward."""
    src_git = os.path.join(remote, ".git")
    if not os.path.isdir(src_git):
        print(f"ERROR: '{remote}' no es un repositorio pygit/git")
        sys.exit(1)
    branch = branch or read_ref(repo)
    head = commit_head(repo)
    if not head:
        print("ERROR: no hay commits para empujar")
        sys.exit(1)
    remote_ref = os.path.join(src_git, "refs", "heads", branch.replace("/", os.sep))

    # Copia solo los objetos que el remoto no tiene
    copied = _copy_objects(repo_dir(repo), os.path.join(src_git, "objects"))
    their_ref = None
    if os.path.exists(remote_ref):
        with open(remote_ref) as f:
            their_ref = f.read().strip()
    if their_ref and their_ref != head:
        # ¿their_ref es ancestro de head? (buskando hacia atrás desde head)
        stack, seen = [head], set()
        ok = False
        while stack:
            sha = stack.pop()
            if sha in seen or not sha:
                continue
            seen.add(sha)
            if sha == their_ref:
                ok = True
                break
            stack.extend(commit_data_parse(repo, sha)[1])
        if not ok:
            print("ERROR: non-fast-forward en "
                  f"'{branch}' (el remoto tiene commits que no tienes; haz merge primero)")
            sys.exit(1)
    os.makedirs(os.path.dirname(remote_ref), exist_ok=True)
    with open(remote_ref, "w", newline="\n") as f:
        f.write(head + "\n")
    print(f"push {branch} -> {remote} ({head[:7]}), {copied} objetos nuevos")


def pull(repo, remote, branch=None):
    """fetch + merge: trae <branch> del remoto y la fusiona en la actual."""
    fetch(repo, remote)
    name = _remote_name(repo, remote)
    branch = branch or read_ref(repo)
    remote_sha_path = repo_dir(repo, "refs", "remotes", name, branch)
    if not os.path.exists(remote_sha_path):
        print(f"ERROR: el remoto '{name}' no tiene la rama '{branch}'")
        sys.exit(1)
    with open(remote_sha_path) as f:
        remote_sha = f.read().strip()
    head = commit_head(repo)
    if head == remote_sha:
        print(f"Ya estás al día con '{name}/{branch}'.")
        return

    # Gcaso1: el commit del remoto desciende de HEAD → fast-forward local
    stack, seen = [remote_sha], set()
    ffable = False
    while stack:
        sha = stack.pop()
        if sha in seen or not sha:
            continue
        seen.add(sha)
        if sha == head:
            ffable = True
            break
        stack.extend(commit_data_parse(repo, sha)[1])

    if ffable:
        # avanza la ref local de la rama y materializa el working tree
        branch_write(repo, branch, remote_sha)
        materialize(repo, remote_sha)
        print(f"pull: fast-forward a {remote_sha[:7]} ({name}/{branch})")
    else:
        # caso divergente: merge del commit traído, reutilizando merge()
        print("pull: historias divergentes; merge del commit del remoto...")
        merge(repo, remote_sha, name=name)
    print(f"pull: '{name}/{branch}' integrado en '{branch}'")


# --- Stash y revert (fase 12, pulido final) ---

STASH_FILE = "pygit-stash"


def stash(repo, message=None):
    """Guarda el estado actual (index+worktree) y lo restaura desde HEAD.

    Equivale a `git stash`: limpiar los cambios sin perderlos.
    """
    staged, unstaged = get_status(repo)
    if not (any(staged) or any(unstaged)):
        print("No hay cambios locales para guardar.")
        return

    # snapshot: {path: sha} del estado mezclado index/disco (disco manda si toca)
    disk = {}
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            with open(os.path.join(root, fn), "rb") as f:
                data = f.read()
            disk[path] = write_object(repo, data, OBJ_BLOB)
    index = dict(index_read(repo))
    # snapshot = archivos del disco con su sha actual
    snapshot = dict(disk)
    # y la lista de lo borrado (estaba en el index, ya no en el disco)
    deleted = [p for p in index if p not in disk]

    base = commit_head(repo)
    if not base:
        print("ERROR: stash sin commits previos")
        sys.exit(1)
    entry = {
        "message": message or f"WIP en {read_ref(repo)}",
        "base": base,
        "snapshot": snapshot,
        "deleted": deleted,
    }
    # apila el entry en el archivo de stash (JSON por línea)
    with open(repo_dir(repo, STASH_FILE), "a") as f:
        f.write(json.dumps(entry) + "\n")

    # restaura el working tree + index desde HEAD
    materialize(repo, base)
    print(f"Guardado stash: {entry['message']}")


def stash_pop(repo):
    """Aplica el último stash y lo saca de la pila."""
    path = repo_dir(repo, STASH_FILE)
    if not os.path.exists(path):
        print("No hay stash que aplicar.")
        sys.exit(1)
    with open(path) as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    if not lines:
        print("No hay stash que aplicar.")
        sys.exit(1)
    entry = json.loads(lines[-1])
    del lines[-1]

    # escribe el snapshot sobre el working tree y el index
    for rel_path, sha in entry["snapshot"].items():
        data = read_object(repo, sha)[1]
        dest = os.path.join(repo, rel_path.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest) or repo, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"Restaurado: {rel_path}")
    for rel_path in entry["deleted"]:
        disk_path = os.path.join(repo, rel_path.replace("/", os.sep))
        if os.path.exists(disk_path):
            os.remove(disk_path)
        print(f"Restaurado borrado: {rel_path}")
    index_write(repo, list(entry["snapshot"].items()))

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    remaining = len(lines)
    print(f"Stash '{entry['message']}' aplicado ({remaining} en la pila)")


def revert(repo):
    """Restaura los cambios NO committeados desde HEAD (git checkout -- .).

    ¡Destructivo! El working tree y el index vuelven al último commit.
    """
    head = commit_head(repo)
    if not head:
        print("ERROR: no hay commits a los que volver")
        sys.exit(1)
    materialize(repo, head)
    print(f"Changes revertidos a {head[:7]}")


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


# --- Diff y branch (fase 6) ---

def diff(repo):
    """Diff unificado entre HEAD y el working tree (como git diff)."""
    head_tree = commit_index(repo, commit_head(repo))
    changed = False
    # Archivos según el último commit
    for path, sha in sorted(head_tree.items()):
        _, old_blob = read_object(repo, sha)
        disk_path = os.path.join(repo, path.replace("/", os.sep))
        if os.path.exists(disk_path):
            with open(disk_path, "rb") as f:
                new_data = f.read()
        else:
            new_data = b""
        if new_data != old_blob:
            changed = True
            _print_diff(path, old_blob, new_data)
    # Archivos nuevos en disco que no están en HEAD
    for root, dirs, filenames in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_rel = os.path.relpath(root, repo).replace(os.sep, "/")
        for fn in filenames:
            path = fn if root_rel == "." else f"{root_rel}/{fn}"
            if path not in head_tree:
                changed = True
                with open(os.path.join(root, fn), "rb") as f:
                    new_data = f.read()
                _print_diff(path, b"", new_data)
    if not changed:
        print("Sin diferencias con respecto a HEAD.")


def _print_diff(path, old_data, new_data):
    print(f"diff --git a/{path} b/{path}")
    # ignora diferencias de salto de línea final al comparar líneas
    old_lines = old_data.decode(errors="replace").splitlines(keepends=True)
    new_lines = new_data.decode(errors="replace").splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    for line in diff_lines:
        sys.stdout.write(line if line.endswith("\n") else line + "\n")


def branch_list(repo, all_refs=False):
    """Lista las ramas (y con -a también las remotas... y refs/tags); marca la actual."""
    current = read_ref(repo)
    heads = repo_dir(repo, "refs", "heads")
    if os.path.isdir(heads):
        for name in sorted(os.listdir(heads)):
            marker = "*" if name == current else " "
            print(f"{marker} {name}")
    if all_refs:
        tags_dir = repo_dir(repo, "refs", "tags")
        if os.path.isdir(tags_dir):
            print("\nTags:")
            for name in sorted(os.listdir(tags_dir)):
                print(f"  tags/{name}")


def branch_delete(repo, name):
    """Borra la rama <name> (no la actual; sin commit propio = no existente)."""
    if name == read_ref(repo):
        print(f"ERROR: no se puede borrar la rama actual '{name}'")
        sys.exit(1)
    path = repo_dir(repo, "refs", "heads", name)
    if not os.path.exists(path):
        print(f"ERROR: la rama '{name}' no existe")
        sys.exit(1)
    os.remove(path)
    print(f"Rama '{name}' borrada")


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

    p_add = sub.add_parser("add", help="Añade archivos al index (staging)")
    p_add.add_argument("paths", nargs="+", help="Archivos, directorios o '.'")

    p_rm = sub.add_parser("rm", help="Borra del index y del working tree")
    p_rm.add_argument("paths", nargs="+", help="Archivos o directorios")

    p_checkout = sub.add_parser("checkout", help="Cambia de rama (la crea si no existe)")
    p_checkout.add_argument("branch", help="Rama destino")

    p_merge = sub.add_parser("merge", help="Fusiona <rama> en la rama actual")
    p_merge.add_argument("branch", help="Rama a fusionar")

    sub.add_parser("diff", help="Diff unificado entre HEAD y el working tree")

    p_branch = sub.add_parser("branch", help="Lista las ramas")
    p_branch.add_argument("-d", "--delete", metavar="RAMA", help="Borra la rama")
    p_branch.add_argument("-a", "--all", action="store_true",
                          help="Lista también los tags")

    p_tag = sub.add_parser("tag", help="Lista tags (o crea con -a/-l)")
    p_tag.add_argument("name", nargs="?", help="Nombre del tag a crear")
    p_tag.add_argument("-a", "--annotate", action="store_true", help="Tag anotado")
    p_tag.add_argument("-m", "--message", help="Mensaje del tag anotado")
    p_tag.add_argument("-d", "--delete", help="Borra el tag")
    p_tag.add_argument("target", nargs="?", default=None,
                       help="Commit/rama destino (tag ligero)")

    p_clone = sub.add_parser("clone", help="Clona un repositorio local")
    p_clone.add_argument("source")
    p_clone.add_argument("target")

    p_fetch = sub.add_parser("fetch", help="Copia objetos y refs de otro repo local")
    p_fetch.add_argument("remote")

    p_push = sub.add_parser("push", help="Empuja a otro repo local (fast-forward)")
    p_push.add_argument("remote")
    p_push.add_argument("branch", nargs="?", default=None)

    p_pull = sub.add_parser("pull", help="fetch + merge de otro repo local")
    p_pull.add_argument("remote")
    p_pull.add_argument("branch", nargs="?", default=None)

    p_stash = sub.add_parser("stash", help="Guarda los cambios y limpia el worktree")
    p_stash.add_argument("message", nargs="?", default=None)
    sub.add_parser("stash-pop", help="Aplica y quita el último stash")
    sub.add_parser("revert", help="Revierte worktree+index al último commit")

    args = parser.parse_args(argv)

    if args.command == "init":
        repo_init(args.repo)
    elif args.command == "commit":
        commit(".", args.message)
    elif args.command == "log":
        log(".")
    elif args.command == "status":
        staged, unstaged = get_status(".")

        def _section(title, triple, verbs=("add", "delete", "modify")):
            if not any(triple):
                return
            print(f"\nCambios {title}:")
            for verb, names in zip(verbs, triple):
                for name in names:
                    print(f"\t{verb}: {name}")

        _section("preparados para el commit (staged)", staged)
        _section("no preparados (unstaged/untracked)", unstaged)
        if not (any(staged) or any(unstaged)):
            print("Sin cambios.")

    elif args.command == "checkout":
        checkout(".", args.branch)
    elif args.command == "add":
        add(".", args.paths)
    elif args.command == "rm":
        remove(".", args.paths)
    elif args.command == "merge":
        merge(".", args.branch)
    elif args.command == "diff":
        diff(".")
    elif args.command == "branch":
        if args.delete:
            branch_delete(".", args.delete)
        else:
            branch_list(".", all_refs=args.all)
    elif args.command == "tag":
        if args.name and not args.delete:
            if args.annotate or args.message:
                tag_annotate(".", args.name, args.message or f"Tag {args.name}")
            else:
                tag_lightweight(".", args.name, args.target)
        elif args.delete:
            tag_delete(".", args.delete)
        else:
            tag_list(".")
    elif args.command == "clone":
        clone(args.source, args.target)
    elif args.command == "fetch":
        fetch(".", args.remote)
    elif args.command == "push":
        push(".", args.remote, args.branch)
    elif args.command == "pull":
        pull(".", args.remote, args.branch)
    elif args.command == "stash":
        stash(".", args.message)
    elif args.command == "stash-pop":
        stash_pop(".")
    elif args.command == "revert":
        revert(".")
    else:
        parser.error(f"Comando desconocido: {args.command}")


if __name__ == "__main__":
    main()
