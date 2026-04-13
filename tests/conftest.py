from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def sample_json(tmp_dir: Path):
    p = tmp_dir / "routes.json"
    p.write_text(
        '{"endpoints": ["/api/v1/users", "/data/v1/tenant-configs/${id}"]}',
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_yaml(tmp_dir: Path):
    p = tmp_dir / "routes.yaml"
    p.write_text(
        "routes:\n  - /v1/orders\n  - /v2/products/{sku}\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_python(tmp_dir: Path):
    p = tmp_dir / "service.py"
    p.write_text(
        "import requests\n\n"
        "def get_user(user_id):\n"
        '    resp = requests.get(f"/api/v1/users/{user_id}")\n'
        "    return resp.json()\n\n"
        "def create_user():\n"
        '    return requests.post("/api/v1/users")\n',
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_java(tmp_dir: Path):
    p = tmp_dir / "Controller.java"
    p.write_text(
        "package com.example;\n\n"
        "import org.springframework.web.bind.annotation.*;\n\n"
        "@RestController\n"
        '@RequestMapping("/api")\n'
        "public class Controller {\n\n"
        '    @GetMapping("/v1/users/{id}")\n'
        "    public String getUser(@PathVariable String id) {\n"
        '        return restTemplate.getForObject("/internal/v1/users/" + id, String.class);\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_ts(tmp_dir: Path):
    p = tmp_dir / "api.ts"
    p.write_text(
        'import axios from "axios";\n\n'
        "export async function fetchUsers() {\n"
        '    const res = await axios.get("/api/v1/users");\n'
        "    return res.data;\n"
        "}\n",
        encoding="utf-8",
    )
    return p
