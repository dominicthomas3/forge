"""The Forge Dashboard — Cyberpunk pipeline control center.

Molten amber + cyan-blue aesthetic. Industrial foundry meets digital workshop.
Built on NiceGUI (Python-native, WebSocket-backed, zero build step).

Usage:
    python -m forge.run_dashboard --mode live --task "Your task here"
    python -m forge.run_dashboard --mode review
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import ui

from forge.config import ForgeConfig
from forge.events import STAGE_NAMES, EventBus, EventType, ForgeEvent
from forge.version import FORGE_VERSION, FORGE_CODENAME

logger = logging.getLogger("forge.dashboard")

# ── Forge Design Tokens ──────────────────────────────────────────────────────
FORGE_BG = "#050505"
FORGE_SURFACE = "#0a0a0a"
FORGE_SURFACE_2 = "#111111"
FORGE_BORDER = "#1a1a1a"
FORGE_PRIMARY = "#FF8C00"       # Molten amber
FORGE_PRIMARY_DIM = "#FF8C0040"
FORGE_ACCENT = "#00D4FF"        # Cyan-blue
FORGE_ACCENT_DIM = "#00D4FF30"
FORGE_PASS = "#00FF88"
FORGE_FAIL = "#FF3366"
FORGE_WARN = "#FFD700"
FORGE_TEXT = "#E0E0E0"
FORGE_TEXT_DIM = "#666666"

STAGE_LIST = [
    (1, "JIM", "Jim Analysis", "mdi-brain"),
    (2, "DEEP", "Deep Think", "mdi-lightbulb-on"),
    (3, "IMPL", "Claude Implement", "mdi-code-braces"),
    (4, "REV", "Claude Review", "mdi-magnify-scan"),
    (5, "CONS", "Consensus", "mdi-handshake"),
    (6, "FIX", "Apply Fixes", "mdi-wrench"),
    (7, "TEST", "Stress Test", "mdi-shield-check"),
]

# ── CSS Theme ────────────────────────────────────────────────────────────────
FORGE_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');

:root {{
    --forge-bg: {FORGE_BG};
    --forge-surface: {FORGE_SURFACE};
    --forge-surface-2: {FORGE_SURFACE_2};
    --forge-border: {FORGE_BORDER};
    --forge-primary: {FORGE_PRIMARY};
    --forge-primary-dim: {FORGE_PRIMARY_DIM};
    --forge-accent: {FORGE_ACCENT};
    --forge-accent-dim: {FORGE_ACCENT_DIM};
    --forge-pass: {FORGE_PASS};
    --forge-fail: {FORGE_FAIL};
    --forge-warn: {FORGE_WARN};
    --forge-text: {FORGE_TEXT};
    --forge-text-dim: {FORGE_TEXT_DIM};
}}

* {{
    font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace !important;
    border-radius: 0 !important;
}}

body {{
    background: var(--forge-bg) !important;
    color: var(--forge-text) !important;
    background-image:
        linear-gradient(rgba(255,140,0,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,140,0,0.03) 1px, transparent 1px) !important;
    background-size: 40px 40px !important;
}}

/* Scanline overlay */
body::after {{
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    pointer-events: none;
    z-index: 9999;
    background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,0,0,0.04) 2px,
        rgba(0,0,0,0.04) 4px
    );
}}

/* Override Quasar/NiceGUI defaults */
.q-card, .q-table {{
    background: var(--forge-surface) !important;
    border: 1px solid var(--forge-border) !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}}

.q-header {{
    background: var(--forge-bg) !important;
    border-bottom: 1px solid var(--forge-border) !important;
    box-shadow: none !important;
}}

.q-footer {{
    background: var(--forge-bg) !important;
    border-top: 1px solid var(--forge-border) !important;
    box-shadow: none !important;
}}

.q-tab {{
    color: var(--forge-text-dim) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.15em !important;
    font-size: 11px !important;
    font-weight: 600 !important;
}}

.q-tab--active {{
    color: var(--forge-primary) !important;
}}

.q-tab-panel {{
    padding: 8px !important;
    background: transparent !important;
}}

.q-tabs__content {{
    border-bottom: 1px solid var(--forge-border) !important;
}}

.q-drawer {{
    background: var(--forge-surface) !important;
    border-left: 1px solid var(--forge-primary) !important;
}}

.q-badge {{
    border-radius: 0 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 10px !important;
    letter-spacing: 0.1em !important;
}}

.q-select, .q-input {{
    background: var(--forge-surface-2) !important;
}}

.q-field__label {{
    color: var(--forge-text-dim) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
    font-size: 10px !important;
}}

/* NiceGUI log component */
.nicegui-log {{
    background: var(--forge-surface) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 11px !important;
    color: var(--forge-text) !important;
    border: 1px solid var(--forge-border) !important;
    border-radius: 0 !important;
}}

/* Markdown content */
.nicegui-markdown {{
    color: var(--forge-text) !important;
    font-size: 13px !important;
}}
.nicegui-markdown pre {{
    background: var(--forge-surface-2) !important;
    padding: 12px !important;
    border: 1px solid var(--forge-border) !important;
    border-radius: 0 !important;
}}
.nicegui-markdown code {{
    color: var(--forge-primary) !important;
}}

/* Scrollbars */
::-webkit-scrollbar {{
    width: 6px;
    height: 6px;
}}
::-webkit-scrollbar-track {{
    background: var(--forge-bg);
}}
::-webkit-scrollbar-thumb {{
    background: #333;
    border-radius: 0;
}}
::-webkit-scrollbar-thumb:hover {{
    background: var(--forge-primary);
}}

/* ── Forge Custom Classes ─────────────────────────────────── */

/* Panel with corner brackets */
.forge-panel {{
    position: relative;
    background: var(--forge-surface) !important;
    border: 1px solid var(--forge-border) !important;
    padding: 16px;
}}
.forge-panel::before,
.forge-panel::after {{
    content: '';
    position: absolute;
    width: 12px;
    height: 12px;
    border-color: var(--forge-primary);
    border-style: solid;
}}
.forge-panel::before {{
    top: -1px; left: -1px;
    border-width: 2px 0 0 2px;
}}
.forge-panel::after {{
    bottom: -1px; right: -1px;
    border-width: 0 2px 2px 0;
}}

/* Section label (ALL CAPS, tracked) */
.forge-label {{
    color: var(--forge-text-dim) !important;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    font-size: 10px;
    font-weight: 600;
}}

/* Primary glowing text */
.forge-glow {{
    color: var(--forge-primary) !important;
    text-shadow: 0 0 10px var(--forge-primary-dim), 0 0 20px var(--forge-primary-dim);
}}

/* Accent glowing text */
.forge-glow-accent {{
    color: var(--forge-accent) !important;
    text-shadow: 0 0 10px var(--forge-accent-dim), 0 0 20px var(--forge-accent-dim);
}}

/* Pipeline stage nodes */
.forge-stage {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 8px 12px;
    border: 1px solid var(--forge-border);
    background: var(--forge-surface);
    cursor: pointer;
    transition: all 0.2s ease;
    min-width: 70px;
    position: relative;
}}
.forge-stage:hover {{
    border-color: var(--forge-primary);
    box-shadow: 0 0 8px var(--forge-primary-dim);
}}

.forge-stage.stage-active {{
    border-color: var(--forge-primary);
    box-shadow: 0 0 12px var(--forge-primary-dim), inset 0 0 12px var(--forge-primary-dim);
    animation: forge-pulse 2s ease-in-out infinite;
}}

.forge-stage.stage-completed {{
    border-color: var(--forge-accent);
    box-shadow: 0 0 6px var(--forge-accent-dim);
}}

.forge-stage.stage-failed {{
    border-color: var(--forge-fail);
    box-shadow: 0 0 8px rgba(255,51,102,0.3);
}}

.forge-stage.stage-skipped {{
    border-color: var(--forge-warn);
    opacity: 0.7;
}}

/* Pipeline connector line */
.forge-connector {{
    width: 24px;
    height: 2px;
    background: var(--forge-border);
    align-self: center;
    margin-top: -20px;
}}
.forge-connector.active {{
    background: var(--forge-primary);
    box-shadow: 0 0 6px var(--forge-primary-dim);
}}
.forge-connector.completed {{
    background: var(--forge-accent);
    box-shadow: 0 0 4px var(--forge-accent-dim);
}}

/* Animations */
@keyframes forge-pulse {{
    0%, 100% {{ box-shadow: 0 0 8px var(--forge-primary-dim), inset 0 0 8px var(--forge-primary-dim); }}
    50% {{ box-shadow: 0 0 20px var(--forge-primary-dim), inset 0 0 20px var(--forge-primary-dim); }}
}}

@keyframes forge-spin {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
}}

/* Spinning loader icon (mdi-loading doesn't auto-spin in Quasar) */
.q-icon[class*="mdi-loading"] {{
    animation: forge-spin 1s linear infinite;
}}

/* Title bar buttons */
.forge-title-btn {{
    width: 32px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--forge-text-dim);
    border: 1px solid transparent;
    transition: all 0.15s ease;
    background: transparent;
}}
.forge-title-btn:hover {{
    color: var(--forge-text);
    border-color: var(--forge-border);
    background: var(--forge-surface-2);
}}
.forge-title-btn.close:hover {{
    color: white;
    background: var(--forge-fail);
    border-color: var(--forge-fail);
}}

/* Cycle history row */
.forge-cycle-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 10px;
    border: 1px solid var(--forge-border);
    background: var(--forge-surface);
    transition: border-color 0.15s;
}}
.forge-cycle-row:hover {{
    border-color: var(--forge-primary);
}}

/* Verdict badges */
.verdict-pass {{
    color: var(--forge-pass);
    border: 1px solid var(--forge-pass);
    padding: 1px 8px;
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    font-weight: 600;
}}
.verdict-fail {{
    color: var(--forge-fail);
    border: 1px solid var(--forge-fail);
    padding: 1px 8px;
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    font-weight: 600;
}}
.verdict-running {{
    color: var(--forge-primary);
    border: 1px solid var(--forge-primary);
    padding: 1px 8px;
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    font-weight: 600;
}}

/* Settings section header */
.settings-section {{
    color: var(--forge-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    font-size: 10px;
    font-weight: 600;
    border-bottom: 1px solid var(--forge-border);
    padding-bottom: 4px;
    margin-top: 16px;
    margin-bottom: 8px;
}}

/* Status bar indicator dot */
.status-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    display: inline-block;
}}
.status-dot.connected {{
    background: var(--forge-pass);
    box-shadow: 0 0 6px var(--forge-pass);
}}
.status-dot.disconnected {{
    background: var(--forge-fail);
    box-shadow: 0 0 6px var(--forge-fail);
}}

/* Error panel */
.forge-error-panel {{
    background: rgba(255,51,102,0.05) !important;
    border: 1px solid var(--forge-fail) !important;
    border-left: 3px solid var(--forge-fail) !important;
    padding: 12px;
}}

/* Native window: make title bar draggable */
.forge-titlebar {{
    -webkit-app-region: drag;
}}
.forge-titlebar button,
.forge-titlebar .no-drag {{
    -webkit-app-region: no-drag;
}}
"""


class ForgeDashboard:
    """The Forge — Cyberpunk pipeline control center."""

    def __init__(
        self,
        config: ForgeConfig,
        event_bus: EventBus | None = None,
        mode: str = "review",
    ):
        self.config = config
        self.event_bus = event_bus
        self.mode = mode

        # Pipeline state
        self.current_cycle: int = 0
        self.current_stage: int = 0
        self.pipeline_running: bool = False
        self.start_time: float = 0.0
        self.stage_states: dict[int, str] = {i: "pending" for i in range(1, 8)}
        self.stage_times: dict[int, float] = {}
        self.cycle_history: list[dict[str, Any]] = []
        self.selected_stage: int = 0
        self.codebase_stats: dict[str, Any] = {}
        self.error_log: list[dict[str, Any]] = []
        self.consecutive_clean: int = 0

        # UI element references
        self._log_element: ui.log | None = None
        self._stage_nodes: dict[int, Any] = {}
        self._stage_status_icons: dict[int, Any] = {}
        self._stage_status_labels: dict[int, Any] = {}
        self._stage_time_labels: dict[int, Any] = {}
        self._connectors: dict[int, Any] = {}
        self._cycle_label: Any = None
        self._elapsed_label: Any = None
        self._target_label: Any = None
        self._stage_output_md: Any = None
        self._history_container: Any = None
        self._report_container: Any = None
        self._convergence_label: Any = None
        self._error_container: Any = None
        self._cycle_list_container: Any = None
        self._status_dot: Any = None
        self._status_text: Any = None
        self._update_status_label: Any = None
        self._settings_drawer: Any = None
        self._timer: Any = None
        self._event_subscribed: bool = False

        # History tab state
        self._history_cycle_select: Any = None
        self._history_output: Any = None

        # Load existing data for review mode
        if mode == "review":
            self._load_existing_cycles()

    # ── UI Construction ──────────────────────────────────────────────────

    def build(self) -> None:
        """Construct the full Forge dashboard UI."""
        ui.dark_mode(True)

        # Inject CSS theme
        ui.add_head_html(f"<style>{FORGE_CSS}</style>")

        # ── Custom Title Bar ──────────────────────────────────────
        with ui.header().classes("q-pa-none").style(
            f"height: auto; background: {FORGE_BG} !important;"
        ):
            # Top row: logo + info + controls
            with ui.row().classes("w-full items-center no-wrap q-px-md forge-titlebar").style(
                f"height: 40px; border-bottom: 1px solid {FORGE_BORDER};"
            ):
                # Left: THE FORGE
                ui.html(
                    f'<span style="font-size:14px; font-weight:700; '
                    f'color:{FORGE_PRIMARY}; text-shadow: 0 0 10px {FORGE_PRIMARY_DIM}; '
                    f'letter-spacing:0.2em;">THE FORGE</span>'
                )
                ui.html(
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'margin-left:12px; letter-spacing:0.1em;">v{FORGE_VERSION}</span>'
                )

                ui.space()

                # Center info strip
                self._cycle_label = ui.html(
                    self._make_info_chip("CYCLE", "0")
                )
                self._elapsed_label = ui.html(
                    self._make_info_chip("ELAPSED", "00:00:00")
                )
                self._target_label = ui.html(
                    self._make_info_chip("TARGET", self.config.target_project.name)
                )

                ui.space()

                # Right: window controls
                ui.button(
                    icon="mdi-cog",
                    on_click=lambda: self._settings_drawer.toggle(),
                ).props("flat dense size=sm").style(
                    f"color: {FORGE_TEXT_DIM};"
                ).tooltip("Configuration")
                ui.button(
                    icon="mdi-window-minimize",
                    on_click=lambda: ui.run_javascript("window.minimize && window.minimize()"),
                ).props("flat dense size=sm").style(
                    f"color: {FORGE_TEXT_DIM};"
                ).tooltip("Minimize")
                ui.button(
                    icon="mdi-close",
                    on_click=lambda: ui.run_javascript(
                        "if(confirm('Close The Forge?')) window.close()"
                    ),
                ).props("flat dense size=sm").style(
                    f"color: {FORGE_TEXT_DIM};"
                ).tooltip("Close")

        # ── Settings Drawer ───────────────────────────────────────
        self._settings_drawer = ui.right_drawer(value=False, bordered=True).style(
            f"width: 340px; background: {FORGE_SURFACE} !important; "
            f"border-left: 1px solid {FORGE_PRIMARY} !important;"
        ).props("overlay")
        with self._settings_drawer:
            self._build_settings_panel()

        # ── Tabs ──────────────────────────────────────────────────
        with ui.tabs().classes("w-full").style(
            f"background: {FORGE_BG};"
        ).props(
            f'active-color="{FORGE_PRIMARY}" indicator-color="{FORGE_PRIMARY}" '
            f'dense align="left"'
        ) as tabs:
            live_tab = ui.tab("LIVE", icon="mdi-play-circle")
            history_tab = ui.tab("HISTORY", icon="mdi-history")
            report_tab = ui.tab("REPORT", icon="mdi-file-document")

        # ── Tab Panels ────────────────────────────────────────────
        with ui.tab_panels(tabs, value=live_tab).classes("w-full").props("animated").style(
            f"background: transparent;"
        ):
            with ui.tab_panel(live_tab).classes("q-pa-sm"):
                self._build_live_tab()

            with ui.tab_panel(history_tab).classes("q-pa-sm"):
                self._build_history_tab()

            with ui.tab_panel(report_tab).classes("q-pa-sm"):
                self._build_report_tab()

        # ── Bottom Status Bar ─────────────────────────────────────
        with ui.footer().style(
            f"height: 28px; background: {FORGE_BG} !important; "
            f"border-top: 1px solid {FORGE_BORDER};"
        ):
            with ui.row().classes("w-full items-center no-wrap q-px-md").style(
                "height: 28px;"
            ):
                ui.html(
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'letter-spacing:0.1em;">THE FORGE v{FORGE_VERSION}</span>'
                )

                ui.html(
                    f'<span style="margin: 0 12px; color:{FORGE_BORDER};">|</span>'
                )

                self._status_dot = ui.html(
                    '<span class="status-dot connected"></span>'
                )
                self._status_text = ui.html(
                    f'<span style="font-size:9px; color:{FORGE_PASS}; '
                    f'letter-spacing:0.1em; margin-left:6px;">CONNECTED</span>'
                )

                ui.html(
                    f'<span style="margin: 0 12px; color:{FORGE_BORDER};">|</span>'
                )

                self._update_status_label = ui.html(
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'letter-spacing:0.1em;">forge-updates: up to date</span>'
                )

                ui.space()

                self._convergence_label = ui.html(
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'letter-spacing:0.1em;">CONVERGENCE: 0/{self.config.convergence_threshold}</span>'
                )

        # ── Timer for elapsed clock ───────────────────────────────
        self._timer = ui.timer(1.0, self._update_elapsed)

        # Subscribe to events (only once — guard against multiple build() calls on page reload)
        if self.event_bus and not self._event_subscribed:
            self._event_subscribed = True
            self.event_bus.subscribe_async(self._handle_event)

    # ── Tab Builders ─────────────────────────────────────────────────────

    def _build_live_tab(self) -> None:
        """Build the live pipeline monitoring view."""
        # Pipeline stepper
        with ui.element("div").classes("forge-panel w-full q-mb-sm"):
            ui.label("PIPELINE").classes("forge-label q-mb-sm")
            with ui.row().classes("w-full justify-center items-center no-wrap gap-none q-pa-xs").style(
                "overflow-x: auto;"
            ):
                for stage_num, abbrev, name, icon_name in STAGE_LIST:
                    # Stage node
                    with ui.element("div").classes("forge-stage").on(
                        "click", lambda n=stage_num: self._select_stage(n)
                    ) as node:
                        self._stage_nodes[stage_num] = node
                        # Stage number
                        ui.html(
                            f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                            f'letter-spacing:0.1em;">{stage_num}</span>'
                        )
                        # Icon
                        self._stage_status_icons[stage_num] = ui.icon(
                            icon_name, size="xs"
                        ).style(f"color: {FORGE_TEXT_DIM};")
                        # Abbreviation
                        self._stage_status_labels[stage_num] = ui.html(
                            f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                            f'letter-spacing:0.1em; font-weight:600;">{abbrev}</span>'
                        )
                        # Time
                        self._stage_time_labels[stage_num] = ui.html(
                            f'<span style="font-size:9px; color:{FORGE_TEXT_DIM};">--</span>'
                        )

                    # Connector between stages
                    if stage_num < 7:
                        connector = ui.element("div").classes("forge-connector")
                        self._connectors[stage_num] = connector

        # Main content: Log + Stage Output
        with ui.row().classes("w-full gap-sm").style("height: 30vh; min-height: 200px;"):
            # Left: Live Log (40%)
            with ui.element("div").classes("forge-panel").style("flex: 4; min-width: 0;"):
                with ui.row().classes("items-center q-mb-xs"):
                    ui.label("LOG").classes("forge-label")
                    ui.space()
                self._log_element = ui.log(max_lines=500).classes("w-full").style(
                    "height: calc(30vh - 60px); min-height: 140px;"
                )

            # Right: Stage Output (60%)
            with ui.element("div").classes("forge-panel").style("flex: 6; min-width: 0;"):
                ui.label("STAGE OUTPUT").classes("forge-label q-mb-xs")
                self._stage_output_md = ui.markdown(
                    "*Select a stage to view output*"
                ).classes("w-full").style(
                    "height: calc(30vh - 60px); min-height: 140px; overflow-y: auto; font-size: 12px;"
                )

        # Error overlay container
        self._error_container = ui.column().classes("w-full gap-xs q-mt-xs")

        # Cycle History (bottom)
        with ui.element("div").classes("forge-panel w-full q-mt-sm"):
            ui.label("CYCLES").classes("forge-label q-mb-sm")
            self._cycle_list_container = ui.column().classes("w-full gap-xs")
            self._render_cycle_history()

    def _build_history_tab(self) -> None:
        """Build the historical cycle browser — reactive, refreshable."""
        with ui.element("div").classes("forge-panel w-full"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label("CYCLE BROWSER").classes("forge-label")
                ui.space()
                ui.button(
                    "REFRESH",
                    icon="mdi-refresh",
                    on_click=self._refresh_history,
                ).props("flat dense size=sm").style(
                    f"color: {FORGE_PRIMARY}; font-size: 10px; letter-spacing: 0.1em;"
                )

            self._history_cycle_select = ui.select(
                options=[],
                label="Select Cycle",
                on_change=lambda e: self._load_history_cycle(e.value),
            ).classes("w-64").style(f"color: {FORGE_TEXT};")

            self._history_output = ui.column().classes("w-full q-mt-md")

        # Populate on build
        self._refresh_history()

    def _build_report_tab(self) -> None:
        """Build the morning report viewer — refreshable."""
        with ui.element("div").classes("forge-panel w-full"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label("MORNING REPORT").classes("forge-label")
                ui.space()
                ui.button(
                    "REFRESH",
                    icon="mdi-refresh",
                    on_click=self._refresh_report,
                ).props("flat dense size=sm").style(
                    f"color: {FORGE_PRIMARY}; font-size: 10px; letter-spacing: 0.1em;"
                )

            self._report_container = ui.column().classes("w-full")

        self._refresh_report()

    def _build_settings_panel(self) -> None:
        """Build the settings drawer content."""
        with ui.column().classes("w-full q-pa-md gap-none"):
            # Header
            with ui.row().classes("items-center q-mb-md"):
                ui.html(
                    f'<span style="font-size:12px; font-weight:600; '
                    f'color:{FORGE_PRIMARY}; letter-spacing:0.15em;">CONFIGURATION</span>'
                )
                ui.space()
                ui.button(
                    icon="mdi-close",
                    on_click=lambda: self._settings_drawer.toggle(),
                ).props("flat dense size=sm").style(f"color: {FORGE_TEXT_DIM};")

            # Pipeline section
            ui.label("PIPELINE").classes("settings-section")
            self._settings_field("Max Cycles", str(self.config.max_cycles))
            self._settings_field("Max Hours", str(self.config.max_wall_hours))
            self._settings_field("Convergence", str(self.config.convergence_threshold))
            self._settings_field("Git Checkpoint", "ON" if self.config.git_checkpoint else "OFF")

            # Target section
            ui.label("TARGET").classes("settings-section")
            self._settings_field("Project Path", str(self.config.target_project))
            self._settings_field("Data Directory", str(self.config.forge_data_dir.name))

            # Interface section
            ui.label("INTERFACE").classes("settings-section")
            self._settings_field("Log Max Lines", "500")
            self._settings_field("Auto-scroll", "ON")
            self._settings_field("Scanlines", "ON")

            # Updates section
            ui.label("UPDATES").classes("settings-section")
            self._settings_field("Current Version", FORGE_VERSION)
            ui.button(
                "CHECK FOR UPDATES",
                on_click=self._check_for_updates,
            ).props("flat dense").style(
                f"color: {FORGE_PRIMARY}; font-size: 10px; letter-spacing: 0.1em; "
                f"border: 1px solid {FORGE_BORDER}; margin-top: 8px; width: 100%;"
            )

            # About section
            ui.label("ABOUT").classes("settings-section")
            ui.html(
                f'<div style="font-size:11px; color:{FORGE_TEXT_DIM}; line-height:1.8;">'
                f'The Forge v{FORGE_VERSION} "{FORGE_CODENAME}"<br>'
                f'7-Stage Autonomous Pipeline<br>'
                f'Multi-Model Development Engine'
                f'</div>'
            )

    # ── Settings Helpers ──────────────────────────────────────────────────

    def _settings_field(self, label: str, value: str) -> None:
        """Render a read-only settings field."""
        with ui.row().classes("w-full items-center q-py-xs"):
            ui.html(
                f'<span style="font-size:11px; color:{FORGE_TEXT_DIM}; '
                f'min-width:120px; display:inline-block;">{label}</span>'
            )
            ui.html(
                f'<span style="font-size:11px; color:{FORGE_TEXT}; '
                f'padding: 2px 8px; border: 1px solid {FORGE_BORDER}; '
                f'background: {FORGE_SURFACE_2}; max-width:180px; '
                f'overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{value}</span>'
            )

    async def _check_for_updates(self) -> None:
        """Trigger update check via UpdateManager."""
        try:
            import os
            from update.manager import UpdateManager
            supabase_url = os.environ.get("SUPABASE_URL", "")
            supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
            if not supabase_url or not supabase_key:
                ui.notify("Supabase credentials not configured", type="warning")
                return
            mgr = UpdateManager(
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                project_root=Path(__file__).parent.parent,
            )
            result = await mgr.check_for_update()
            if result and result.get("update_available"):
                ver = result.get("version", "?")
                if self._update_status_label:
                    self._update_status_label._props["innerHTML"] = (
                        f'<span style="font-size:9px; color:{FORGE_WARN}; '
                        f'letter-spacing:0.1em;">UPDATE AVAILABLE: v{ver}</span>'
                    )
                    # ObservableDict auto-updates
                ui.notify(f"Update available: v{ver}", type="warning")
            else:
                ui.notify("Already up to date", type="positive")
        except Exception as e:
            ui.notify(f"Update check failed: {e}", type="negative")

    # ── Info Chip Helper ──────────────────────────────────────────────────

    @staticmethod
    def _make_info_chip(label: str, value: str) -> str:
        return (
            f'<span style="margin: 0 8px;">'
            f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
            f'letter-spacing:0.1em;">{label}: </span>'
            f'<span style="font-size:11px; color:{FORGE_TEXT};">{value}</span>'
            f'</span>'
        )

    # ── Event Handling ───────────────────────────────────────────────────

    async def _handle_event(self, event: ForgeEvent) -> None:
        """Process a pipeline event and update the UI."""
        try:
            await self._dispatch_event(event)
        except Exception:
            logger.error("Event handler error for %s", event.event_type, exc_info=True)

    async def _dispatch_event(self, event: ForgeEvent) -> None:
        """Inner event dispatch — separated for clean exception boundary."""
        etype = event.event_type

        if etype == EventType.PIPELINE_STARTED:
            self.pipeline_running = True
            self.start_time = event.timestamp
            self._reset_stages()

        elif etype == EventType.CYCLE_STARTED:
            self.current_cycle = event.cycle
            self._reset_stages()
            if self._cycle_label:
                self._cycle_label._props["innerHTML"] = self._make_info_chip(
                    "CYCLE", str(event.cycle)
                )
                # ObservableDict auto-updates

        elif etype == EventType.STAGE_STARTED:
            stage = event.stage
            if stage:
                self.current_stage = stage
                self.stage_states[stage] = "running"
                self._update_stage_node(stage, "running")

        elif etype == EventType.STAGE_COMPLETED:
            stage = event.stage
            if stage:
                self.stage_states[stage] = "completed"
                elapsed = event.data.get("elapsed", 0)
                self.stage_times[stage] = elapsed
                self._update_stage_node(stage, "completed", elapsed)
                # Auto-load output only if user hasn't manually selected a different stage
                output_path = event.data.get("output_path")
                if output_path and (self.selected_stage == 0 or self.selected_stage == stage):
                    self._load_stage_output(Path(output_path))

        elif etype == EventType.STAGE_FAILED:
            stage = event.stage or self.current_stage
            if stage:
                self.stage_states[stage] = "failed"
                self._update_stage_node(stage, "failed")
            self._show_error(event)

        elif etype == EventType.STAGE_SKIPPED:
            stage = event.stage
            if stage:
                self.stage_states[stage] = "skipped"
                self._update_stage_node(stage, "skipped")

        elif etype == EventType.VERDICT:
            verdict = event.data.get("verdict", "UNCLEAR")
            color = FORGE_PASS if verdict == "PASS" else FORGE_FAIL if verdict == "FAIL" else FORGE_WARN
            if self._cycle_label:
                self._cycle_label._props["innerHTML"] = (
                    f'<span style="margin: 0 8px;">'
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'letter-spacing:0.1em;">CYCLE: </span>'
                    f'<span style="font-size:11px; color:{FORGE_TEXT};">{event.cycle}</span>'
                    f'<span style="font-size:10px; color:{color}; margin-left:8px; '
                    f'font-weight:600;">{verdict}</span>'
                    f'</span>'
                )
                # ObservableDict auto-updates

        elif etype == EventType.CODEBASE_LOADED:
            char_count = event.data.get("char_count", 0)
            est_tokens = char_count // 4
            if self._target_label:
                self._target_label._props["innerHTML"] = self._make_info_chip(
                    "TARGET",
                    f"{self.config.target_project.name} (~{est_tokens:,} tok)"
                )
                # ObservableDict auto-updates

        elif etype == EventType.CYCLE_COMPLETED:
            self.cycle_history.append({
                "cycle": event.cycle,
                "verdict": event.data.get("verdict", "ERROR"),
                "stages": event.data.get("stages_completed", 0),
                "errors": event.data.get("errors", 0),
                "timestamp": datetime.fromtimestamp(event.timestamp),
            })
            self._render_cycle_history()

        elif etype == EventType.PIPELINE_FINISHED:
            self.pipeline_running = False
            self.consecutive_clean = event.data.get("consecutive_clean", 0)
            if self._convergence_label:
                threshold = self.config.convergence_threshold
                self._convergence_label._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                    f'letter-spacing:0.1em;">CONVERGENCE: '
                    f'{self.consecutive_clean}/{threshold}</span>'
                )
                # ObservableDict auto-updates
            report_path = event.data.get("report_path")
            if report_path:
                self._refresh_report()

    # ── UI Update Helpers ────────────────────────────────────────────────

    def _reset_stages(self) -> None:
        """Reset all stage nodes to pending state."""
        for stage_num in range(1, 8):
            self.stage_states[stage_num] = "pending"
            self._update_stage_node(stage_num, "pending")

    def _update_stage_node(
        self, stage: int, state: str, elapsed: float = 0
    ) -> None:
        """Update visual state of a pipeline stage node."""
        node = self._stage_nodes.get(stage)
        icon_el = self._stage_status_icons.get(stage)
        time_el = self._stage_time_labels.get(stage)

        if not node:
            return

        # Remove all state classes
        for cls in ["stage-active", "stage-completed", "stage-failed", "stage-skipped"]:
            node.classes(remove=cls)

        # Update connector to the left of this stage
        if stage > 1:
            connector = self._connectors.get(stage - 1)
            if connector:
                connector.classes(remove="active completed")

        if state == "running":
            node.classes(add="stage-active")
            if icon_el:
                icon_el.props("name=mdi-loading")
                icon_el._style["color"] = FORGE_PRIMARY  # ObservableDict auto-updates
            if time_el:
                time_el._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_PRIMARY};">...</span>'
                )
                # ObservableDict auto-updates
            # Light up connector
            if stage > 1:
                connector = self._connectors.get(stage - 1)
                if connector:
                    connector.classes(add="active")

        elif state == "completed":
            node.classes(add="stage-completed")
            if icon_el:
                icon_el.props("name=mdi-check")
                icon_el._style["color"] = FORGE_ACCENT  # ObservableDict auto-updates
            if time_el:
                time_el._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_ACCENT};">'
                    f'{self._format_elapsed(elapsed)}</span>'
                )
                # ObservableDict auto-updates
            if stage > 1:
                connector = self._connectors.get(stage - 1)
                if connector:
                    connector.classes(add="completed")

        elif state == "failed":
            node.classes(add="stage-failed")
            if icon_el:
                icon_el.props("name=mdi-close")
                icon_el._style["color"] = FORGE_FAIL  # ObservableDict auto-updates
            if time_el:
                time_el._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_FAIL};">FAIL</span>'
                )
                # ObservableDict auto-updates

        elif state == "skipped":
            node.classes(add="stage-skipped")
            if icon_el:
                icon_el.props("name=mdi-skip-next")
                icon_el._style["color"] = FORGE_WARN  # ObservableDict auto-updates
            if time_el:
                time_el._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_WARN};">SKIP</span>'
                )
                # ObservableDict auto-updates

        else:  # pending
            stage_info = STAGE_LIST[stage - 1]
            if icon_el:
                icon_el.props(f"name={stage_info[3]}")
                icon_el._style["color"] = FORGE_TEXT_DIM  # ObservableDict auto-updates
            if time_el:
                time_el._props["innerHTML"] = (
                    f'<span style="font-size:9px; color:{FORGE_TEXT_DIM};">--</span>'
                )
                # ObservableDict auto-updates

    def _select_stage(self, stage_num: int) -> None:
        """Handle click on a stage node — load its output."""
        self.selected_stage = stage_num
        cycle = self.current_cycle or self._get_latest_cycle_num()
        if cycle <= 0:
            return

        stage_files = {
            1: "01-jim-analysis.md",
            2: "02-deep-think-verification.md",
            3: "03-claude-implementation.log",
            4: "04-claude-review.md",
            5: "05-consensus.md",
            6: "06-fixes-applied.log",
            7: "07-stress-test.md",
        }
        filename = stage_files.get(stage_num)
        if filename:
            path = self.config.forge_data_dir / f"cycle-{cycle:03d}" / filename
            self._load_stage_output(path)

    def _load_stage_output(self, path: Path) -> None:
        """Load a stage output file into the stage output panel."""
        if not self._stage_output_md:
            return
        if not path.exists():
            self._stage_output_md.set_content(
                f"*Output not yet available: `{path.name}`*"
            )
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50_000:
                content = content[:50_000] + "\n\n... (truncated for display)"
            if path.suffix == ".log":
                # Escape triple backticks to prevent markdown code block breakage
                safe_content = content.replace("```", "` ` `")
                self._stage_output_md.set_content(f"```\n{safe_content}\n```")
            else:
                self._stage_output_md.set_content(content)
        except Exception as e:
            self._stage_output_md.set_content(f"*Error reading output: {e}*")

    def _show_error(self, event: ForgeEvent) -> None:
        """Show a prominent error card when a stage fails (capped at 10 visible)."""
        self.error_log.append({
            "time": datetime.fromtimestamp(event.timestamp),
            "cycle": event.cycle,
            "stage": event.stage,
            "error": event.data.get("error", "Unknown error"),
            "error_type": event.data.get("error_type", "Unknown"),
        })
        # Cap error_log to last 50 entries
        if len(self.error_log) > 50:
            self.error_log = self.error_log[-50:]

        if not self._error_container:
            return

        # Cap visible error panels to 10 — remove oldest
        while len(self._error_container.default_slot.children) >= 10:
            self._error_container.remove(0)

        with self._error_container:
            with ui.element("div").classes("forge-error-panel"):
                with ui.row().classes("items-center gap-sm"):
                    ui.icon("mdi-alert-circle", size="xs").style(
                        f"color: {FORGE_FAIL};"
                    )
                    stage_name = STAGE_NAMES.get(event.stage or 0, "Unknown")
                    ui.html(
                        f'<span style="font-size:11px; color:{FORGE_FAIL}; '
                        f'font-weight:600; letter-spacing:0.1em;">'
                        f'STAGE {event.stage}: {stage_name} — FAILED</span>'
                    )
                    ui.space()
                    error_type = html_mod.escape(str(event.data.get("error_type", "ERROR")))
                    ui.html(
                        f'<span style="font-size:9px; color:{FORGE_FAIL}; '
                        f'border:1px solid {FORGE_FAIL}; padding:1px 6px; '
                        f'letter-spacing:0.1em;">'
                        f'{error_type}</span>'
                    )
                error_msg = html_mod.escape(str(event.data.get("error", "")))
                ui.html(
                    f'<div style="font-size:11px; color:{FORGE_TEXT_DIM}; '
                    f'margin-top:6px;">{error_msg}</div>'
                )

                stderr = event.data.get("stderr", "")
                if stderr:
                    with ui.expansion("STDERR", icon="mdi-console").style(
                        f"color: {FORGE_TEXT_DIM}; font-size:10px;"
                    ):
                        ui.code(stderr[:5000], language="text").style(
                            f"font-size:10px; background:{FORGE_SURFACE_2} !important;"
                        )

    def _render_cycle_history(self) -> None:
        """Render the cycle history timeline in the Live tab."""
        if not self._cycle_list_container:
            return

        self._cycle_list_container.clear()
        with self._cycle_list_container:
            if not self.cycle_history:
                ui.html(
                    f'<span style="font-size:11px; color:{FORGE_TEXT_DIM};">'
                    f'No cycles completed yet.</span>'
                )
                return

            for entry in reversed(self.cycle_history[-20:]):
                verdict = entry.get("verdict", "ERROR")
                stages = entry.get("stages", 0)
                errors = entry.get("errors", 0)
                cycle_num = entry.get("cycle", 0)
                ts = entry.get("timestamp")
                time_str = ts.strftime("%H:%M:%S") if isinstance(ts, datetime) else ""

                if verdict == "PASS":
                    badge_class = "verdict-pass"
                elif verdict == "FAIL":
                    badge_class = "verdict-fail"
                else:
                    badge_class = "verdict-running"

                errors_html = ""
                if errors:
                    errors_html = (
                        f'<span style="font-size:10px; color:{FORGE_FAIL};">'
                        f'{errors} errors</span>'
                    )

                with ui.element("div").classes("forge-cycle-row"):
                    ui.html(
                        f'<span class="{badge_class}">{verdict}</span>'
                        f'<span style="font-size:10px; color:{FORGE_TEXT}; margin-left:12px;">'
                        f'Cycle {cycle_num}</span>'
                        f'<span style="font-size:10px; color:{FORGE_TEXT_DIM}; margin-left:12px;">'
                        f'{stages}/7 stages</span>'
                        f'{errors_html}'
                        f'<span style="font-size:10px; color:{FORGE_TEXT_DIM}; margin-left:auto;">'
                        f'{time_str}</span>'
                    )

    def _update_elapsed(self) -> None:
        """Update the elapsed time display (called every second by timer)."""
        if self.pipeline_running and self.start_time:
            elapsed = time.time() - self.start_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            if self._elapsed_label:
                self._elapsed_label._props["innerHTML"] = self._make_info_chip(
                    "ELAPSED", f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                )
                # ObservableDict auto-updates

    # ── History Tab Helpers ───────────────────────────────────────────────

    def _refresh_history(self) -> None:
        """Refresh the cycle list in the History tab."""
        if not self.config.forge_data_dir.exists():
            existing_cycles = []
        else:
            existing_cycles = sorted(
                d for d in self.config.forge_data_dir.glob("cycle-*") if d.is_dir()
            )
        cycle_names = [d.name for d in existing_cycles]

        if self._history_cycle_select:
            self._history_cycle_select.options = cycle_names
            self._history_cycle_select.update()
            if cycle_names:
                self._history_cycle_select.value = cycle_names[-1]
                self._load_history_cycle(cycle_names[-1])
            else:
                if self._history_output:
                    self._history_output.clear()
                    with self._history_output:
                        ui.html(
                            f'<span style="font-size:11px; color:{FORGE_TEXT_DIM};">'
                            f'No completed cycles found.</span>'
                        )

    def _load_history_cycle(self, cycle_name: str | None) -> None:
        """Load a specific cycle's data in the History tab."""
        if not cycle_name or not self._history_output:
            return

        self._history_output.clear()
        cycle_dir = self.config.forge_data_dir / cycle_name

        if not cycle_dir.exists():
            with self._history_output:
                ui.html(
                    f'<span style="color:{FORGE_TEXT_DIM};">Cycle directory not found.</span>'
                )
            return

        stage_files = sorted(
            list(cycle_dir.glob("*.md")) + list(cycle_dir.glob("*.log"))
        )

        with self._history_output:
            if not stage_files:
                ui.html(
                    f'<span style="color:{FORGE_TEXT_DIM};">No stage outputs found.</span>'
                )
                return

            # Verdict badge
            stress_file = cycle_dir / "07-stress-test.md"
            if stress_file.exists():
                try:
                    content = stress_file.read_text(encoding="utf-8", errors="replace")
                    from forge.orchestrator import Orchestrator
                    verdict = Orchestrator._detect_verdict(content)
                    color = FORGE_PASS if verdict == "PASS" else FORGE_FAIL if verdict == "FAIL" else FORGE_WARN
                    ui.html(
                        f'<span style="font-size:10px; color:{color}; '
                        f'border:1px solid {color}; padding:2px 10px; '
                        f'letter-spacing:0.1em; font-weight:600; '
                        f'margin-bottom:12px; display:inline-block;">{verdict}</span>'
                    )
                except Exception:
                    pass

            # File tabs
            with ui.tabs().style(
                f"background: transparent;"
            ).props(
                f'active-color="{FORGE_PRIMARY}" indicator-color="{FORGE_PRIMARY}" dense'
            ) as stage_tabs:
                tab_map = {}
                for sf in stage_files:
                    tab_map[sf.name] = ui.tab(sf.stem).style(
                        f"font-size:10px; letter-spacing:0.1em;"
                    )

            with ui.tab_panels(
                stage_tabs,
                value=list(tab_map.values())[0] if tab_map else None,
            ).classes("w-full").style("background: transparent;"):
                for sf in stage_files:
                    with ui.tab_panel(tab_map[sf.name]):
                        try:
                            text = sf.read_text(encoding="utf-8", errors="replace")
                            if len(text) > 50_000:
                                text = text[:50_000] + "\n\n... (truncated)"
                            if sf.suffix == ".md":
                                ui.markdown(text).style(
                                    "max-height: 600px; overflow-y: auto;"
                                )
                            else:
                                ui.code(text, language="text").style(
                                    f"max-height: 600px; overflow-y: auto; "
                                    f"font-size: 11px; background: {FORGE_SURFACE_2} !important;"
                                )
                        except Exception as e:
                            ui.html(
                                f'<span style="color:{FORGE_FAIL};">'
                                f'Error reading file: {e}</span>'
                            )

    # ── Report Tab Helpers ────────────────────────────────────────────────

    def _refresh_report(self) -> None:
        """Refresh the morning report display."""
        if not self._report_container:
            return

        self._report_container.clear()
        report_path = self.config.forge_data_dir / "morning-report.md"

        with self._report_container:
            if report_path.exists():
                try:
                    content = report_path.read_text(encoding="utf-8", errors="replace")
                    mtime = datetime.fromtimestamp(report_path.stat().st_mtime)
                    ui.html(
                        f'<span style="font-size:9px; color:{FORGE_TEXT_DIM}; '
                        f'letter-spacing:0.1em; margin-bottom:8px; display:block;">'
                        f'GENERATED: {mtime.strftime("%Y-%m-%d %H:%M:%S")}</span>'
                    )
                    ui.markdown(content).style(
                        "max-height: 80vh; overflow-y: auto;"
                    )
                except Exception as e:
                    ui.html(
                        f'<span style="color:{FORGE_FAIL};">'
                        f'Error reading report: {e}</span>'
                    )
            else:
                ui.html(
                    f'<div style="color:{FORGE_TEXT_DIM}; text-align:center; '
                    f'padding: 60px 20px;">'
                    f'<div style="font-size:24px; margin-bottom:12px;">&#9878;</div>'
                    f'<div style="font-size:12px; letter-spacing:0.1em;">NO REPORT GENERATED YET</div>'
                    f'<div style="font-size:11px; margin-top:8px;">'
                    f'Run the pipeline to generate a morning report.</div>'
                    f'</div>'
                )

    # ── Data Loaders ─────────────────────────────────────────────────────

    def _load_existing_cycles(self) -> None:
        """Load cycle history from existing .forge_data/ directories."""
        if not self.config.forge_data_dir.exists():
            return
        cycle_dirs = sorted(
            d for d in self.config.forge_data_dir.glob("cycle-*") if d.is_dir()
        )
        for cycle_dir in cycle_dirs:
            try:
                cycle_num = int(cycle_dir.name.split("-")[1])
                stress_file = cycle_dir / "07-stress-test.md"
                stage_files = list(cycle_dir.glob("0*.md")) + list(cycle_dir.glob("0*.log"))
                stages_completed = len(stage_files)

                verdict = "INCOMPLETE"
                if stress_file.exists() and stress_file.stat().st_size >= 50:
                    from forge.orchestrator import Orchestrator
                    content = stress_file.read_text(encoding="utf-8", errors="replace")
                    verdict = Orchestrator._detect_verdict(content)

                self.cycle_history.append({
                    "cycle": cycle_num,
                    "verdict": verdict,
                    "stages": stages_completed,
                    "errors": 0,
                    "timestamp": datetime.fromtimestamp(cycle_dir.stat().st_mtime),
                })
            except Exception:
                continue

    def _get_latest_cycle_num(self) -> int:
        """Get the highest cycle number from existing data."""
        if not self.config.forge_data_dir.exists():
            return 0
        cycle_dirs = sorted(
            d for d in self.config.forge_data_dir.glob("cycle-*") if d.is_dir()
        )
        if not cycle_dirs:
            return 0
        try:
            return int(cycle_dirs[-1].name.split("-")[1])
        except (IndexError, ValueError):
            return 0

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format elapsed seconds into human-readable string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m{secs}s"


# ── Log Handler Bridge ───────────────────────────────────────────────────────

class DashboardLogHandler(logging.Handler):
    """Custom logging handler that pushes forge.* logs to the NiceGUI ui.log.

    Thread-safe: captures the event loop at construction time and uses
    call_soon_threadsafe to push log messages from any thread.
    """

    def __init__(self, log_element: ui.log, loop: asyncio.AbstractEventLoop | None = None):
        super().__init__()
        self.log_element = log_element
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self.log_element.push, msg)
            else:
                self.log_element.push(msg)
        except Exception:
            pass


def attach_log_handler(dashboard: ForgeDashboard) -> None:
    """Attach the dashboard log handler to the forge logger.

    Removes any previously attached DashboardLogHandler to prevent
    handler accumulation on page reloads/reconnects.
    """
    if dashboard._log_element is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    handler = DashboardLogHandler(dashboard._log_element, loop=loop)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))

    forge_logger = logging.getLogger("forge")
    # Remove stale DashboardLogHandlers from previous page loads
    for existing in forge_logger.handlers[:]:
        if isinstance(existing, DashboardLogHandler):
            forge_logger.removeHandler(existing)
    forge_logger.addHandler(handler)
