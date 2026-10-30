"""代码结构索引：AST 解析 + SQLite 持久化 + FTS5 全文索引。

将源码解析为「符号（symbol）」粒度：文件、类、函数/方法，
并记录起止行号、签名、docstring、导入关系与函数调用关系。
"""
from __future__ import annotations

import ast
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .repo import RepoMeta

INDEX_DIR_NAME = ".replens"
DB_FILE_NAME = "index.db"


@dataclass
class Symbol:
    """一个可被定位、可作为证据的代码符号。"""
    path: str          # 相对仓库根的文件路径
    type: str          # module / class / function / method
    name: str          # 符号名（module 用文件路径表示）
    start_line: int
    end_line: int
    signature: str = ""
    doc: str = ""
    code: str = ""

    @property
    def ref(self) -> str:
        """证据引用形式：path:start-end 或 path:start。"""
        return f"{self.path}:{self.start_line}"


class _SymbolCollector(ast.NodeVisitor):
    """遍历单个文件，收集符号与调用关系。"""

    def __init__(self, path: str, source: str, lines: List[str]):
        self.path = path
        self.source = source
        self.lines = lines
        self.symbols: List[Symbol] = []
        self.calls: List[tuple] = []  # (caller_path, caller_name, callee_name, line)
        self._class_stack: List[str] = []
        self._func_stack: List[str] = []

    def _code(self, start: int, end: int) -> str:
        if start < 1 or start > len(self.lines):
            return ""
        return "\n".join(self.lines[start - 1:end]).strip()

    def _signature(self, node: ast.AST) -> str:
        """生成人类可读签名：def name(args) / class name。"""
        try:
            if isinstance(node, ast.ClassDef):
                return "class " + node.name
            if hasattr(node, "args"):
                args = ast.unparse(node.args)
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                return prefix + " " + node.name + "(" + args + ")"
        except Exception:
            pass
        return getattr(node, "name", "")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.symbols.append(Symbol(
            path=self.path, type="class", name=node.name,
            start_line=node.lineno, end_line=node.end_lineno or node.lineno,
            signature=self._signature(node),
            doc=ast.get_docstring(node) or "",
            code=self._code(node.lineno, node.end_lineno or node.lineno),
        ))
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _visit_func(self, node) -> None:
        in_class = bool(self._class_stack)
        stype = "method" if in_class else "function"
        self.symbols.append(Symbol(
            path=self.path, type=stype, name=node.name,
            start_line=node.lineno, end_line=node.end_lineno or node.lineno,
            signature=self._signature(node),
            doc=ast.get_docstring(node) or "",
            code=self._code(node.lineno, node.end_lineno or node.lineno),
        ))
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._callee_name(node.func)
        if callee and self._func_stack:
            self.calls.append((self.path, self._func_stack[-1], callee, node.lineno))
        self.generic_visit(node)

    def _callee_name(self, func: ast.AST) -> Optional[str]:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None


def _collect_imports(tree: ast.AST) -> List[tuple]:
    """收集文件级导入关系 (module, imported_names)。"""
    imports: List[tuple] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append((f"{mod}.{alias.name}" if mod else alias.name,
                                alias.asname or alias.name))
    return imports


class IndexDB:
    """SQLite 索引库，封装 symbols / imports / calls / FTS5。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._fts_available = False
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT,
                doc TEXT,
                code TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                module TEXT NOT NULL,
                name TEXT
            );

            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_path TEXT NOT NULL,
                caller_name TEXT NOT NULL,
                callee_name TEXT NOT NULL,
                line INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_name);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS symbol_embeddings (
                symbol_id INTEGER PRIMARY KEY,
                vector BLOB NOT NULL,
                dim INTEGER NOT NULL,
                model TEXT NOT NULL
            );
            """
        )
        # FTS5 全文索引（普通表，非 external content；rowid 与 symbols.id 对齐）
        try:
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts
                USING fts5(name, doc, code)
                """
            )
            self._fts_available = True
        except sqlite3.OperationalError:
            # 某些精简编译的 SQLite 不带 FTS5，自动降级到 LIKE 查询
            self._fts_available = False
        self.conn.commit()

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    def clear(self) -> None:
        cur = self.conn.cursor()
        for table in ("symbols", "imports", "calls", "symbol_embeddings"):
            cur.execute(f"DELETE FROM {table}")
        if self._fts_available:
            cur.execute("DELETE FROM symbols_fts")
        self.conn.commit()

    def insert_symbols(self, symbols: Iterable[Symbol]) -> None:
        """批量插入 symbols，并用一条 INSERT...SELECT 同步到 FTS5（rowid=id 自动对齐）。"""
        rows = [(s.path, s.type, s.name, s.start_line, s.end_line,
                 s.signature, s.doc, s.code) for s in symbols]
        self.conn.executemany(
            "INSERT INTO symbols(path,type,name,start_line,end_line,"
            "signature,doc,code) VALUES(?,?,?,?,?,?,?,?)", rows)
        if self._fts_available:
            self.conn.execute(
                "INSERT INTO symbols_fts(rowid, name, doc, code) "
                "SELECT id, name, doc, code FROM symbols")
        self.conn.commit()

    def insert_imports(self, imports: Iterable[tuple], path: str) -> None:
        rows = [(path, m, n) for (m, n) in imports]
        self.conn.executemany(
            "INSERT INTO imports(path,module,name) VALUES(?,?,?)", rows)
        self.conn.commit()

    def insert_calls(self, calls: Iterable[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO calls(caller_path,caller_name,callee_name,line) VALUES(?,?,?,?)",
            list(calls))
        self.conn.commit()

    SCHEMA_VERSION = "2"

    def is_current(self, fingerprint: str) -> bool:
        """索引是否与给定源码指纹一致（用于复用判断）。"""
        try:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key='src_fingerprint'").fetchone()
            ver = self.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:
            return False
        return bool(row and row[0] == fingerprint and
                    ver and ver[0] == self.SCHEMA_VERSION and
                    self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] > 0)

    def set_fingerprint(self, fingerprint: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('src_fingerprint', ?)",
            (fingerprint,))
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (self.SCHEMA_VERSION,))
        self.conn.commit()

    def save_embedding(self, symbol_id: int, vector, model: str) -> None:
        import struct
        dim = len(vector)
        blob = struct.pack(f"{dim}f", *vector)
        self.conn.execute(
            "INSERT OR REPLACE INTO symbol_embeddings(symbol_id, vector, dim, model) "
            "VALUES(?,?,?,?)", (symbol_id, blob, dim, model))
        self.conn.commit()

    def load_embeddings(self, model: str) -> dict:
        """加载某模型下的全部符号向量缓存。返回 {symbol_id: [float, ...]}。"""
        import struct
        out = {}
        try:
            rows = self.conn.execute(
                "SELECT symbol_id, vector, dim FROM symbol_embeddings WHERE model=?",
                (model,)).fetchall()
        except sqlite3.OperationalError:
            return out
        for r in rows:
            dim = r["dim"]
            try:
                vec = list(struct.unpack(f"{dim}f", bytes(r["vector"])))
            except Exception:
                continue
            out[r["symbol_id"]] = vec
        return out

    def all_symbol_ids(self):
        try:
            return [r["id"] for r in self.conn.execute(
                "SELECT id FROM symbols ORDER BY id").fetchall()]
        except sqlite3.OperationalError:
            return []

    def close(self) -> None:
        self.conn.close()


def _src_fingerprint(meta: RepoMeta) -> str:
    """计算源码和文档指纹，用于判断索引是否过期。"""
    root = Path(meta.path)
    parts = []
    for rel in meta.python_files + getattr(meta, "document_files", []):
        try:
            content = (root / rel).read_bytes()
            parts.append(rel + ":" + hashlib.sha256(content).hexdigest())
        except OSError:
            continue
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def build_index(meta: RepoMeta, db_path: Optional[str] = None,
                force: bool = False) -> IndexDB:
    """对仓库执行索引构建。

    若已存在与当前源码一致的索引且 force=False，则直接复用（不重建）。
    """
    root = Path(meta.path)
    if db_path is None:
        db_path = str(root / INDEX_DIR_NAME / DB_FILE_NAME)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    fingerprint = _src_fingerprint(meta)
    db = IndexDB(db_path)

    # 复用已有索引
    if not force and db.is_current(fingerprint):
        return db

    db.clear()

    all_symbols: List[Symbol] = []
    all_calls: List[tuple] = []

    for rel in meta.python_files:
        fp = root / rel
        try:
            source = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            # 语法错误的文件跳过，不阻塞整体索引
            continue

        # module 级符号
        all_symbols.append(Symbol(
            path=rel, type="module", name=rel,
            start_line=1, end_line=max(len(lines), 1),
            signature="", doc=ast.get_docstring(tree) or "",
            code=source,
        ))

        collector = _SymbolCollector(rel, source, lines)
        collector.visit(tree)
        all_symbols.extend(collector.symbols)
        all_calls.extend(collector.calls)

        db.insert_imports(_collect_imports(tree), rel)

    # 文档作为独立证据索引，保留相对路径、行号和全文内容。
    for rel in getattr(meta, "document_files", []):
        fp = root / rel
        try:
            source = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = source.splitlines()
        all_symbols.append(Symbol(
            path=rel, type="document", name=rel,
            start_line=1, end_line=max(len(lines), 1),
            signature="document", doc="", code=source,
        ))

    db.insert_symbols(all_symbols)
    db.insert_calls(all_calls)
    db.set_fingerprint(fingerprint)
    return db
