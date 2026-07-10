"""Generic Pipeline Engine ? reusable execution framework.
Abstracts control flow, error handling, and state file persistence.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List
from pydantic import BaseModel, Field


STATE_FILE = "docs/MACRO_OS_STATE.md"


class PipelineContext(BaseModel):
    """Data bus for pipeline execution."""
    data: Dict[str, Any] = Field(default_factory=dict)
    is_aborted: bool = False
    errors: List[str] = Field(default_factory=list)


class PipelineNode(ABC):
    """Abstract pipeline node. Override execute() in subclasses."""
    name: str = "UnnamedNode"

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> bool:
        ...


class PipelineEngine:
    """Sequential node executor with state file management."""

    def __init__(self, nodes: List[PipelineNode], state_file: str = "docs/MACRO_OS_STATE.md"):
        self.nodes = nodes
        self.state_file = state_file

    def run(self, ctx: PipelineContext) -> bool:
        """Execute nodes sequentially. Returns True if all passed."""
        for node in self.nodes:
            if ctx.is_aborted:
                break
            try:
                ok = node.execute(ctx)
                if not ok:
                    ctx.errors.append(f"[{node.name}] Node returned False")
                    ctx.is_aborted = True
            except Exception as e:
                ctx.errors.append(f"[{node.name}] {e}")
                ctx.is_aborted = True
        self._update_state_file(ctx)
        return not ctx.is_aborted

    def _update_state_file(self, ctx: PipelineContext):
        """Sync state to MARKDOWN state file after each pipeline run."""
        import os
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Build the Known Issues section
        known_issues = ""
        if ctx.errors:
            known_issues += "| Time | Node | Error |\n"
            known_issues += "|------|------|-------|\n"
            for err in ctx.errors:
                parts = err.split("] ", 1)
                node = parts[0].lstrip("[")
                msg = parts[1] if len(parts) > 1 else err
                known_issues += f"| {ts} | {node} | {msg} |\n"
        else:
            known_issues = "_No known issues. Last run: %s_\n" % ts

        # Read or create state file
        if os.path.exists(self.state_file):
            with open(self.state_file, "r", encoding="utf-8") as sf:
                content = sf.read()
        else:
            content = "# Macro OS ? Daily Pipeline State\n\n"
            content += "## Last Pipeline Run\n\n"
            content += "Status: _Never run_\n\n"
            content += "## Known Issues\n\n"

        # Replace Known Issues section
        import re
        marker = "## Known Issues"
        if marker in content:
            before = content[: content.index(marker)]
            after = content[content.index(marker) :]
            after_lines = after.split("\n")
            # Find start of next section or end of file
            next_section = len(after_lines)
            for i, line in enumerate(after_lines[1:], 1):
                if line.startswith("## "):
                    next_section = i
                    break
            tail = "\n".join(after_lines[next_section:])
            content = before + marker + "\n\n" + known_issues + "\n" + tail
        else:
            content += marker + "\n\n" + known_issues + "\n"

        # Update status line
        status = "? Passed" if not ctx.errors else "? Failed (%d errors)" % len(ctx.errors)
        cline = "Status: _%s_\n" % status
        import re as re2
        if "Status:" in content:
            content = re2.sub(r"Status: .*\n", cline, content)
        else:
            content = content.replace("# Macro OS", "# Macro OS\n\n" + cline)

        with open(self.state_file, "w", encoding="utf-8") as sf:
            sf.write(content)


class PineAnalysisNode(PipelineNode):
    """Fetch a Pine script conclusion from TradingView and stage it for scoring.

    Bridges macro-os to the live chart via adapters.TradingViewAdapter.fetch_pine_conclusions
    (which shells out to relay/pine-bridge.mjs over CDP). The resulting PineConclusionSchema
    is stored on the pipeline context so later nodes (scoring / decision kernel) can consume
    it as a confirmation / divergence signal.
    """

    name = "pine_analysis"

    def __init__(self, adapter, symbol: str | None = None, script_name: str | None = None):
        self.adapter = adapter
        self.symbol = symbol
        self.script_name = script_name

    def execute(self, ctx: PipelineContext) -> bool:
        conclusion = self.adapter.fetch_pine_conclusions(
            symbol=self.symbol,
            script_name=self.script_name,
        )
        if conclusion is None:
            ctx.errors.append("[pine_analysis] No Pine conclusion available")
            return False

        ctx.data["pine_conclusion"] = conclusion.model_dump()
        signals = ctx.data.setdefault("signals", {})
        signals["pine"] = conclusion.model_dump()
        return True
