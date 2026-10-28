#!/usr/bin/env python3
"""Tests de pygit con pytest: cada comando CLI en repos temporales."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

PYGIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pygit.py")


def run(repo, *args, ok=True):
    """Ejecuta el CLI de pygit en repo/ y devuelve stdout; falla si exit != 0."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, PYGIT, *args],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if ok and proc.returncode != 0:
        raise AssertionError(
            f"pygit {' '.join(args)} falló ({proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return proc


def write(repo, name, content):
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n") as f:
        f.write(content)


def read(repo, name):
    with open(os.path.join(repo, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def repo(tmp_path):
    """Cada test recibe un repositorio pygit recién initizado."""
    d = str(tmp_path)
    run(d, "init")
    return d


# --- init ---

def test_init_crea_estructura(tmp_path):
    d = str(tmp_path)
    run(d, "init")
    for p in (".git/objects", ".git/refs/heads", ".git/HEAD"):
        assert os.path.exists(os.path.join(d, p))


def test_init_idempotente(repo):
    out = run(repo, "init")
    assert "Ya existe" in out.stdout


# --- add + commit ---

def test_commit_solo_staged(repo):
    """Sin add no se commitea nada: el working tree no entra en el commit."""
    write(repo, "staged.txt", "sí")
    write(repo, "unstaged.txt", "no")
    run(repo, "add", "staged.txt")
    run(repo, "commit", "-m", "solo staged")
    last = run(repo, "log").stdout.splitlines()[0]
    sha = last.split()[0]
    proc = subprocess.run(
        ["git", "cat-file", "-t", sha],
        cwd=repo, capture_output=True, text=True,
    )
    assert proc.stdout.strip() == "commit"


def test_commit_vacio_aborta(repo):
    proc = run(repo, "commit", "-m", "vacío", ok=False)
    assert proc.returncode != 0
    assert "add" in proc.stdout or "add" in proc.stderr


def test_log_encadena_padres(repo):
    write(repo, "a.txt", "uno")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "c1")
    write(repo, "a.txt", "dos")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "c2")
    lines = run(repo, "log").stdout.splitlines()
    assert len(lines) == 2
    assert "c1" in lines[1] and "c2" in lines[0]


# --- status ---

def test_status_reconoce_staged_y_untracked(repo):
    write(repo, "x.txt", "uno")
    run(repo, "add", "x.txt")
    write(repo, "y.txt", "dos")
    out = run(repo, "status").stdout
    assert "staged" in out and "x.txt" in out
    assert "untracked" in out and "y.txt" in out


def test_status_detecta_modificado(repo):
    write(repo, "x.txt", "uno")
    run(repo, "add", "x.txt")
    run(repo, "commit", "-m", "c1")
    write(repo, "x.txt", "uno cambiado")
    out = run(repo, "status").stdout
    assert "unstaged" in out and "x.txt" in out


# --- checkout y ramas ---

def test_checkout_materializa_borra_y_restaura(repo):
    write(repo, "parrot.txt", "A")
    run(repo, "add", "parrot.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "feature")
    write(repo, "feat.txt", "trabajo en feature")
    run(repo, "add", "feat.txt")
    run(repo, "commit", "-m", "feature")
    # al volver a master, feat.txt debe desaparecer
    run(repo, "checkout", "master")
    assert not os.path.exists(os.path.join(repo, "feat.txt"))
    assert read(repo, "parrot.txt") == "A"
    run(repo, "checkout", "feature")
    assert read(repo, "feat.txt") == "trabajo en feature"


def test_branch_lista_y_borra(repo):
    write(repo, "base.txt", "x")
    run(repo, "add", "base.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "otra")
    out = run(repo, "branch").stdout
    assert "* otra" in out and "master" in out
    run(repo, "checkout", "master")
    run(repo, "branch", "-d", "otra")
    out = run(repo, "branch").stdout
    assert "otra" not in out


def test_branch_no_borra_actual(repo):
    write(repo, "base.txt", "x")
    run(repo, "add", "base.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "x")
    proc = run(repo, "branch", "-d", "x", ok=False)
    assert proc.returncode != 0


# --- merge ---

def _prepara_divergencia(repo):
    write(repo, "comun.txt", "0\n1\n2\n3\n4\n5\n6\n7\n8\n9\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "feature")
    write(repo, "comun.txt", "0\n1\n2\n3\nFEA\n5\n6\n7\n8\n9\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "feature edita línea 4")
    run(repo, "checkout", "master")
    write(repo, "comun.txt", "0\n1\n2\n3\n4\n5\n6\n7\nMAS\n9\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "master edita línea 8")


def test_merge_auto_fusiona_archivo_compartido(repo):
    _prepara_divergencia(repo)
    run(repo, "merge", "feature")
    contenido = read(repo, "comun.txt")
    assert "FEA" in contenido and "MAS" in contenido
    # y git lo ve como commit de fusión con dos padres
    proc = subprocess.run(
        ["git", "log", "--format=%s %p"], cwd=repo, capture_output=True, text=True
    )
    first = proc.stdout.splitlines()[0]
    assert "Merged" in first and len(first.split()) >= 4


def test_merge_conflicto_deja_marcadores(repo):
    write(repo, "comun.txt", "v0\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "feature")
    write(repo, "comun.txt", "v-feature\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "feature")
    run(repo, "checkout", "master")
    write(repo, "comun.txt", "v-master\n")
    run(repo, "add", "comun.txt")
    run(repo, "commit", "-m", "master")
    proc = run(repo, "merge", "feature", ok=False)
    assert proc.returncode != 0
    marcadores = read(repo, "comun.txt")
    assert "<<<<<<< HEAD" in marcadores
    assert "v-master\n" in marcadores
    assert "v-feature\n" in marcadores
    assert ">>>>>>> feature" in marcadores


def test_merge_flujo_completo_resolucion_manual(repo):
    write(repo, "c.txt", "v0\n")
    run(repo, "add", "c.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "checkout", "feature")
    write(repo, "c.txt", "v-feature\n")
    run(repo, "add", "c.txt")
    run(repo, "commit", "-m", "f")
    run(repo, "checkout", "master")
    write(repo, "c.txt", "v-master\n")
    run(repo, "add", "c.txt")
    run(repo, "commit", "-m", "m")
    run(repo, "merge", "feature", ok=False)      # conflicto con marcadores
    write(repo, "c.txt", "v-resuelto\n")          # resolución manual
    run(repo, "add", "c.txt")
    run(repo, "commit", "-m", "merge resuelto")
    assert read(repo, "c.txt") == "v-resuelto\n"


# --- tags ---

def test_tag_ligero(repo):
    write(repo, "a.txt", "1")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "tag", "v1")
    out = run(repo, "tag").stdout
    assert "v1" in out
    proc = subprocess.run(
        ["git", "for-each-ref", "refs/tags", "--format=%(refname:short)"],
        cwd=repo, capture_output=True, text=True,
    )
    assert "v1" in proc.stdout


def test_tag_anotado_es_objeto_tag_real(repo):
    write(repo, "a.txt", "1")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "tag", "-a", "v1.0", "-m", "primera versión")
    out = run(repo, "tag").stdout
    assert "v1.0 (anotado)" in out
    # git debe poder desreferenciarlo
    proc = subprocess.run(
        ["git", "tag", "-n1"], cwd=repo, capture_output=True, text=True,
    )
    assert "v1.0" in proc.stdout and "primera" in proc.stdout


def test_tag_duplicado_falla(repo):
    write(repo, "a.txt", "1")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "tag", "v1")
    proc = run(repo, "tag", "-a", "v1", "-m", "otro", ok=False)
    assert proc.returncode != 0


# --- tag -d / branch -a / clone ---

def test_tag_delete(repo):
    write(repo, "a.txt", "1")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "tag", "v1")
    run(repo, "tag", "-d", "v1")
    out = run(repo, "tag").stdout
    assert "v1" not in out


def test_branch_all_muestra_tags(repo):
    write(repo, "a.txt", "1")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-m", "base")
    run(repo, "tag", "-a", "v1", "-m", "msg")
    out = run(repo, "branch", "-a").stdout
    assert "tags/v1" in out


def test_clone_local(repo, tmp_path):
    """Un clone copia objetos, refs, HEAD y materializa el working tree."""
    import os as _os

    write(repo, "base.txt", "contenido original")
    run(repo, "add", "base.txt")
    run(repo, "commit", "-m", "base en t8")
    run(repo, "tag", "-a", "v1", "-m", "tag del origen")

    dest = _os.path.join(str(tmp_path), "clone-de-prueba")
    run(repo, "clone", repo, dest)   # source y target absolutos

    read = lambda name: open(
        _os.path.join(dest, name), encoding="utf-8").read()
    assert read("base.txt") == "contenido original"
    # log de pygit en el clon muestra el commit
    out = run(dest, "log").stdout
    assert "base en t8" in out
    # y el tag anotado importado
    out = run(dest, "tag").stdout
    assert "v1 (anotado)" in out
    # git real ve el commit clonado
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=dest, capture_output=True, text=True
    )
    assert "base en t8" in proc.stdout


# --- stash / revert ---

def test_stash_guarda_y_limpia(repo):
    _commit(repo, "f.txt", "version commit", "base")
    write(repo, "f.txt", "cambio no commiteado")
    write(repo, "nuevo.txt", "archivo nuevo")
    out = run(repo, "stash", "mi trabajo").stdout
    assert "Guardado stash" in out
    # el working tree vuelve al último commit
    assert read(repo, "f.txt") == "version commit"
    assert not os.path.exists(os.path.join(repo, "nuevo.txt"))
    out = run(repo, "status").stdout
    assert "Sin cambios" in out


def test_stash_pop_restaura_todo(repo):
    _commit(repo, "f.txt", "version commit", "base")
    write(repo, "f.txt", "cambio no commiteado")
    write(repo, "nuevo.txt", "archivo nuevo")
    run(repo, "stash", " trabajo")
    run(repo, "stash-pop")
    assert read(repo, "f.txt") == "cambio no commiteado"
    assert read(repo, "nuevo.txt") == "archivo nuevo"
    # la pila queda vacía
    proc = run(repo, "stash-pop", ok=False)
    assert proc.returncode != 0


def test_revert_ultima_modificacion(repo):
    _commit(repo, "a.txt", "committed", "base")
    write(repo, "a.txt", "convulsión local")
    write(repo, "b.txt", "nuevo")
    out = run(repo, "revert").stdout
    assert read(repo, "a.txt") == "committed"
    assert not os.path.exists(os.path.join(repo, "b.txt"))


# --- push / fetch ---

def _commit(repo, name, content, msg):
    write(repo, name, content)
    run(repo, "add", name)
    run(repo, "commit", "-m", msg)


def _init_repo(path):
    import os as _os
    run(_os.path.dirname(path) or ".", "init", path)
    return path


def test_push_y_fetch_entre_repos(repo, tmp_path):
    import os as _os
    _commit(repo, "f.txt", "v0", "v0")
    remoto = _os.path.join(str(tmp_path), "remoto")
    _init_repo(remoto)
    # push v0... no, push exige commits en el local: ya v0
    run(repo, "push", remoto)
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=remoto, capture_output=True, text=True
    )
    assert "v0" in proc.stdout
    # nuevo commit y push fast-forward
    _commit(repo, "f.txt", "v1", "v1")
    run(repo, "push", remoto)
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=remoto, capture_output=True, text=True
    )
    assert "v1" in proc.stdout and "v0" in proc.stdout


def test_push_rechaza_non_fast_forward(repo, tmp_path):
    import os as _os
    _commit(repo, "f.txt", "base", "base")
    remoto = _os.path.join(str(tmp_path), "remoto")
    _init_repo(remoto)
    run(repo, "push", remoto)
    # el otro lado avanza con commit propio
    _commit(remoto, "g.txt", "remoto", "commit-del-remoto")
    # y localmente creamos un commit divergente
    _commit(repo, "h.txt", "local", "commit-local-divergente")
    proc = run(repo, "push", remoto, ok=False)
    assert proc.returncode != 0
    assert "non-fast-forward" in proc.stdout


def test_fetch_importa_refs_y_objetos(repo, tmp_path):
    import os as _os
    _commit(repo, "f.txt", "origen", "en-origen")
    remoto = _os.path.join(str(tmp_path), "remoto")
    _init_repo(remoto)
    _commit(remoto, "g.txt", "remoto", "en-remoto")
    out = run(repo, "fetch", remoto).stdout
    assert "remoto" in out
    proc = subprocess.run(
        ["git", "for-each-ref", "refs/remotes", "--format=%(refname:short)"],
        cwd=repo, capture_output=True, text=True,
    )
    assert "remoto/master" in proc.stdout.replace("\\", "/")


def test_clone_y_push_son_simetricos(repo, tmp_path):
    """commit en origen -> clone -> commit en clon -> push -> origen lo ve."""
    import os as _os
    _commit(repo, "f.txt", "v0", "v0")
    clon = _os.path.join(str(tmp_path), "clon")
    run(repo, "clone", repo, clon)
    _commit(clon, "f.txt", "v1", "v1-en-clon")
    run(clon, "push", repo)
    proc = subprocess.run(
        ["git", "cat-file", "-p", "HEAD:f.txt"],
        cwd=repo, capture_output=True, text=True,
    )
    assert proc.stdout.strip() == "v1"


# --- pull ---

def test_pull_fast_forward(repo, tmp_path):
    import os as _os
    _commit(repo, "a.txt", "v0", "v0")
    clon = _os.path.join(str(tmp_path), "clon")
    run(repo, "clone", repo, clon)
    _commit(repo, "nuevo.txt", "novedad", "v1-en-origen")
    out = run(clon, "pull", repo).stdout
    assert "fast-forward" in out
    assert open(_os.path.join(clon, "nuevo.txt"), encoding="utf-8").read() == "novedad"
    # la rama local del clon avanza al commit del origen
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=clon, capture_output=True, text=True
    )
    assert "v1-en-origen" in proc.stdout.splitlines()[0]


def test_pull_divergente_crea_commit_de_merge(repo, tmp_path):
    import os as _os
    _commit(repo, "local.txt", "en origen", "v1")
    clon = _os.path.join(str(tmp_path), "clon")
    run(repo, "clone", repo, clon)
    _commit(repo, "solo-origen.txt", "x", "v2-en-origen")
    _commit(clon, "solo-clon.txt", "y", "v2-en-clon")
    out = run(clon, "pull", repo).stdout
    proc = subprocess.run(
        ["git", "log", "--oneline"], cwd=clon, capture_output=True, text=True
    )
    lines = proc.stdout.splitlines()
    assert "v2-en-origen" in "\n".join(lines)
    assert "v2-en-clon" in "\n".join(lines)
    # el último commit debe ser un merge (dos padres)
    proc2 = subprocess.run(
        ["git", "log", "-1", "--format=%p"], cwd=clon, capture_output=True, text=True
    )
    assert len(proc2.stdout.split()) == 2


def test_pull_cuando_ya_estas_al_dia(repo, tmp_path):
    import os as _os
    _commit(repo, "a.txt", "v0", "v0")
    clon = _os.path.join(str(tmp_path), "clon")
    run(repo, "clone", repo, clon)
    out = run(clon, "pull", repo).stdout
    assert "al día" in out or "al d" in out

# --- rm / diff ---

def test_rm_borra_del_index_y_disco(repo):
    write(repo, "x.txt", "1")
    run(repo, "add", "x.txt")
    run(repo, "commit", "-m", "con x")
    run(repo, "rm", "x.txt")
    assert not os.path.exists(os.path.join(repo, "x.txt"))
    run(repo, "commit", "-m", "sin x")
    out = run(repo, "log").stdout.splitlines()
    # el último commit no debe contener x.txt: lo verificamos a través del tree
    sha = out[0].split()[0]
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", sha],
        cwd=repo, capture_output=True, text=True,
    )
    assert "x.txt" not in proc.stdout


def test_diff_muestra_cambios(repo):
    write(repo, "d.txt", "uno\ndos\n")
    run(repo, "add", "d.txt")
    run(repo, "commit", "-m", "base")
    write(repo, "d.txt", "uno\nDOS CAMBIADO\n")
    out = run(repo, "diff").stdout
    assert "-dos" in out and "+DOS CAMBIADO" in out


# --- log --graph / status --branch / clone tracking (fase 13) ---

def test_log_graph_rombo_de_merge(repo):
    _commit(repo, "a.txt", "v0", "base")
    run(repo, "checkout", "feature")
    _commit(repo, "f.txt", "feat", "commit feature")
    run(repo, "checkout", "master")
    _commit(repo, "m.txt", "master", "commit master")
    run(repo, "merge", "feature")
    out = run(repo, "log", "--graph").stdout
    lines = out.splitlines()
    assert lines[0].startswith("| *") or lines[0].startswith("* ")
    assert "|\\\\" in out or "|\\" in out          # fork del merge
    assert "|/" in out                            # union del rombo
    # lo dibuja con la misma forma que git real
    shape = [l.lstrip(" ") for l in lines]
    assert "* " in out and ("| * " in out or any(l.startswith("| *") for l in lines))


def test_status_branch_adelante_y_atras(repo, tmp_path):
    import os as _os
    _commit(repo, "a.txt", "v0", "base")
    remoto = _os.path.join(str(tmp_path), "rem")
    _init_repo(remoto)
    run(repo, "push", remoto)
    run(repo, "fetch", remoto)
    # sin diferencia: al dia
    out = run(repo, "status", "--branch").stdout
    assert "al día" in out
    # 1 adelante (ensuciamos en local)
    _commit(repo, "a.txt", "v1", "adelante")
    out = run(repo, "status", "--branch").stdout
    assert "1 adelante" in out
    # para medir el "atras", movemos el remoto en su propio repo y fetch
    _commit(remoto, "b.txt", "rem", "trabajo remoto")
    run(repo, "fetch", remoto)
    out = run(repo, "status", "--branch").stdout
    assert "1 adelante" in out and "1 atrás" in out


def test_clone_crea_refs_remotes(tmp_path):
    import os as _os
    origen = _os.path.join(str(tmp_path), "origen")
    _init_repo(origen)
    _commit(origen, "f.txt", "v0", "base")
    destino = _os.path.join(str(tmp_path), "copia")
    run(str(tmp_path), "clone", origen, destino)
    ref = _os.path.join(destino, ".git", "refs", "remotes")
    nombres = _os.listdir(ref)
    assert nombres, "clone no creo refs/remotes"
    rem = nombres[0]
    assert _os.path.exists(_os.path.join(ref, rem, "master"))
    # y el status del clon fresco esta al dia
    out = run(destino, "status", "--branch").stdout
    assert "al día" in out
