"""
Plugin API — Extension Framework.

Provides a clean plugin registration and lifecycle system so community
contributors can add: custom ATS handlers, new job platforms, specialized
resume templates, industry-specific archetypes, custom scoring models,
and notification channels — without modifying core code.

Plugins are Python files in the plugins/ directory. Each must define
a register(registry) function that receives the PluginRegistry.
"""

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Optional, Callable

log = logging.getLogger("lla.plugins")


class PluginRegistry:
    """Central registry for plugin extensions."""

    def __init__(self):
        # Extension points — plugins register callables/classes here
        self._ats_handlers: dict[str, object] = {}      # ATS name -> handler class
        self._platforms: dict[str, object] = {}          # platform name -> JobPlatform class
        self._resume_templates: dict[str, str] = {}      # template name -> HTML string
        self._archetypes: dict[str, dict] = {}           # archetype name -> config dict
        self._scorers: dict[str, Callable] = {}          # scorer name -> scoring function
        self._notifiers: dict[str, Callable] = {}        # channel name -> send function
        self._hooks: dict[str, list[Callable]] = {       # hook point -> list of callables
            "pre_apply": [],
            "post_apply": [],
            "pre_score": [],
            "post_score": [],
            "pre_cycle": [],
            "post_cycle": [],
            "on_error": [],
            "on_response": [],
        }
        self._metadata: dict[str, dict] = {}             # plugin name -> metadata

    # ── ATS Handlers ──────────────────────────────────────────

    def register_ats(self, name: str, handler_class):
        """Register a custom ATS handler (e.g., custom Workday variant)."""
        self._ats_handlers[name.lower()] = handler_class
        log.info(f"Plugin: registered ATS handler '{name}'")

    def get_ats_handler(self, name: str):
        return self._ats_handlers.get(name.lower())

    def get_all_ats(self) -> list[str]:
        return list(self._ats_handlers.keys())

    # ── Job Platforms ─────────────────────────────────────────

    def register_platform(self, name: str, platform_class):
        """Register a new job platform (e.g., Naukri, AngelList)."""
        self._platforms[name.lower()] = platform_class
        log.info(f"Plugin: registered platform '{name}'")

    def get_platform(self, name: str):
        return self._platforms.get(name.lower())

    def get_all_platforms(self) -> list[str]:
        return list(self._platforms.keys())

    # ── Resume Templates ──────────────────────────────────────

    def register_template(self, name: str, html_template: str):
        """Register a custom resume HTML template."""
        self._resume_templates[name] = html_template
        log.info(f"Plugin: registered resume template '{name}'")

    def get_template(self, name: str) -> str:
        return self._resume_templates.get(name, "")

    def get_all_templates(self) -> list[str]:
        return list(self._resume_templates.keys())

    # ── Role Archetypes ───────────────────────────────────────

    def register_archetype(self, name: str, config: dict):
        """Register a custom role archetype.
        config: {keywords: [...], emphasis: str, framing: str}
        """
        self._archetypes[name] = config
        log.info(f"Plugin: registered archetype '{name}'")

    def get_archetype(self, name: str) -> dict:
        return self._archetypes.get(name, {})

    def get_all_archetypes(self) -> dict:
        return dict(self._archetypes)

    # ── Custom Scorers ────────────────────────────────────────

    def register_scorer(self, name: str, scorer_fn: Callable):
        """Register a custom scoring function.
        scorer_fn(title, company, description, cv_text) -> {score: int, ...}
        """
        self._scorers[name] = scorer_fn
        log.info(f"Plugin: registered scorer '{name}'")

    def get_scorer(self, name: str) -> Optional[Callable]:
        return self._scorers.get(name)

    # ── Notification Channels ─────────────────────────────────

    def register_notifier(self, name: str, send_fn: Callable):
        """Register a custom notification channel.
        send_fn(message: str) -> bool
        """
        self._notifiers[name] = send_fn
        log.info(f"Plugin: registered notifier '{name}'")

    def get_notifier(self, name: str) -> Optional[Callable]:
        return self._notifiers.get(name)

    def get_all_notifiers(self) -> list[str]:
        return list(self._notifiers.keys())

    # ── Lifecycle Hooks ───────────────────────────────────────

    def register_hook(self, hook_point: str, callback: Callable):
        """Register a callback for a lifecycle hook point.

        Hook points: pre_apply, post_apply, pre_score, post_score,
                     pre_cycle, post_cycle, on_error, on_response
        """
        if hook_point not in self._hooks:
            log.warning(f"Unknown hook point: {hook_point}")
            return
        self._hooks[hook_point].append(callback)
        log.debug(f"Plugin: registered hook '{hook_point}'")

    def fire_hook(self, hook_point: str, **kwargs):
        """Fire all registered callbacks for a hook point."""
        for callback in self._hooks.get(hook_point, []):
            try:
                callback(**kwargs)
            except Exception as e:
                log.warning(f"Plugin hook '{hook_point}' failed: {e}")

    # ── Plugin Metadata ───────────────────────────────────────

    def register_plugin(self, name: str, version: str = "0.1.0",
                        author: str = "", description: str = ""):
        """Register plugin metadata."""
        self._metadata[name] = {
            "name": name,
            "version": version,
            "author": author,
            "description": description,
        }

    def get_loaded_plugins(self) -> list[dict]:
        return list(self._metadata.values())


class PluginLoader:
    """Discovers and loads plugins from the plugins/ directory."""

    def __init__(self, cfg: dict = None, plugins_dir: str = "plugins"):
        self.cfg = cfg or {}
        self.plugins_dir = plugins_dir
        pl_cfg = self.cfg.get("plugins", {})
        self.enabled = pl_cfg.get("enabled", True)
        self.registry = PluginRegistry()
        self._loaded: list[str] = []

    def load_all(self) -> PluginRegistry:
        """Discover and load all plugins from the plugins directory."""
        if not self.enabled:
            return self.registry

        plugins_path = Path(self.plugins_dir)
        if not plugins_path.exists():
            plugins_path.mkdir(parents=True, exist_ok=True)
            # Create example plugin
            self._create_example_plugin(plugins_path)
            return self.registry

        # Load each .py file in plugins/
        for plugin_file in sorted(plugins_path.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            try:
                self._load_plugin(plugin_file)
            except Exception as e:
                log.warning(f"Failed to load plugin {plugin_file.name}: {e}")

        if self._loaded:
            log.info(f"Loaded {len(self._loaded)} plugins: {', '.join(self._loaded)}")

        return self.registry

    def _load_plugin(self, plugin_path: Path):
        """Load a single plugin file."""
        name = plugin_path.stem

        # Check if plugin is disabled in config
        disabled = self.cfg.get("plugins", {}).get("disabled", [])
        if name in disabled:
            log.debug(f"Plugin '{name}' is disabled in config")
            return

        # Import the module
        spec = importlib.util.spec_from_file_location(f"lla_plugin_{name}", plugin_path)
        if spec is None or spec.loader is None:
            return

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Call the register function
        if hasattr(module, "register"):
            module.register(self.registry)
            self._loaded.append(name)
            log.info(f"Loaded plugin: {name}")
        else:
            log.warning(f"Plugin {name} has no register() function — skipping")

    def _create_example_plugin(self, plugins_dir: Path):
        """Create an example plugin as documentation."""
        example = '''"""
Example Plugin — Custom ATS Handler for Bamboo HR.

Place this file in the plugins/ directory. It will be automatically
loaded on startup. Implement the register(registry) function to
hook into the LinkedIn Lightning Applier.
"""


def register(registry):
    """Called automatically by PluginLoader."""

    # Register plugin metadata
    registry.register_plugin(
        name="example-bamboo-ats",
        version="1.0.0",
        author="Your Name",
        description="Custom ATS handler for Bamboo HR applications",
    )

    # Example: Register a custom ATS handler
    # registry.register_ats("bamboohr", BambooHRHandler)

    # Example: Register a lifecycle hook
    # registry.register_hook("post_apply", on_applied)

    # Example: Register a custom notification channel
    # registry.register_notifier("custom_webhook", send_webhook)

    # Example: Register a custom archetype
    # registry.register_archetype("blockchain_engineer", {
    #     "keywords": ["blockchain", "web3", "solidity", "smart contract"],
    #     "emphasis": "distributed systems, cryptography, DeFi",
    # })


# def on_applied(job_id="", title="", company="", **kwargs):
#     """Called after every successful application."""
#     print(f"Applied to {title} at {company}!")


# def send_webhook(message):
#     """Custom notification channel."""
#     import requests
#     requests.post("https://your-webhook.com/notify", json={"text": message})
#     return True
'''
        (plugins_dir / "example_plugin.py").write_text(example)
        log.info(f"Created example plugin at {plugins_dir}/example_plugin.py")

    def get_registry(self) -> PluginRegistry:
        return self.registry
