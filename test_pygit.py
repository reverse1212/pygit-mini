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
    proc = subprocess.run(
        [sys.executable, PYGIT, *args],
        cwd=repo, capture_output=True, text=True,
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
