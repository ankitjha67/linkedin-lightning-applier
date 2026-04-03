"""
Browser Fingerprint Rotation.

Rotates browser fingerprint (canvas, WebGL, timezone, language, screen
resolution, user agent) per session to avoid advanced bot detection.
Complements undetected-chromedriver's basic anti-detection.
"""

import logging
import random

log = logging.getLogger("lla.fingerprint")

# Realistic screen resolutions (common displays)
RESOLUTIONS = [
    (1920, 1080), (1366, 768), (1440, 900), (1536, 864),
    (1280, 720), (1600, 900), (2560, 1440), (1280, 800),
    (1680, 1050), (1920, 1200), (1360, 768), (1280, 1024),
]

# Common timezones for job search regions
TIMEZONES = {
    "us": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
    "uk": ["Europe/London"],
    "eu": ["Europe/Berlin", "Europe/Paris", "Europe/Amsterdam"],
    "india": ["Asia/Kolkata"],
    "singapore": ["Asia/Singapore"],
    "uae": ["Asia/Dubai"],
    "canada": ["America/Toronto", "America/Vancouver"],
    "australia": ["Australia/Sydney", "Australia/Melbourne"],
}

# Realistic user agents (Chrome on Windows/Mac/Linux)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]

# WebGL renderers (must match realistic GPU combos)
WEBGL_RENDERERS = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti, OpenGL 4.5)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Pro 5500M, OpenGL 4.1)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 620, OpenGL 4.1)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)"),
]

# Languages
LANGUAGES = {
    "us": ["en-US", "en"],
    "uk": ["en-GB", "en"],
    "india": ["en-IN", "en"],
    "singapore": ["en-SG", "en"],
    "uae": ["en-AE", "en", "ar"],
    "canada": ["en-CA", "en"],
    "australia": ["en-AU", "en"],
    "eu": ["en-GB", "en", "de"],
}


class FingerprintRotator:
    """Generate and apply randomized browser fingerprints."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        fp_cfg = cfg.get("fingerprint", {})
        self.enabled = fp_cfg.get("enabled", False)
        self.rotate_user_agent = fp_cfg.get("rotate_user_agent", True)
        self.rotate_resolution = fp_cfg.get("rotate_resolution", True)
        self.spoof_webgl = fp_cfg.get("spoof_webgl", True)
        self.spoof_canvas = fp_cfg.get("spoof_canvas", True)
        self.region = fp_cfg.get("region", "us")

        self.current_fingerprint = None

    def generate_fingerprint(self) -> dict:
        """Generate a new random but consistent fingerprint."""
        region = self.region.lower()

        fp = {
            "user_agent": random.choice(USER_AGENTS) if self.rotate_user_agent else None,
            "resolution": random.choice(RESOLUTIONS) if self.rotate_resolution else None,
            "timezone": random.choice(TIMEZONES.get(region, TIMEZONES["us"])),
            "languages": LANGUAGES.get(region, LANGUAGES["us"]),
            "webgl": random.choice(WEBGL_RENDERERS) if self.spoof_webgl else None,
            "canvas_noise": random.uniform(0.0001, 0.001) if self.spoof_canvas else 0,
            "hardware_concurrency": random.choice([2, 4, 8, 12, 16]),
            "device_memory": random.choice([4, 8, 16, 32]),
            "platform": self._pick_platform(),
        }

        self.current_fingerprint = fp
        log.info(f"Fingerprint generated: {fp['resolution']}, "
                f"tz={fp['timezone']}, cores={fp['hardware_concurrency']}")
        return fp

    def _pick_platform(self) -> str:
        """Pick a platform matching the user agent."""
        if not self.current_fingerprint:
            return random.choice(["Win32", "MacIntel", "Linux x86_64"])
        ua = self.current_fingerprint.get("user_agent", "")
        if ua:
            if "Windows" in ua:
                return "Win32"
            elif "Macintosh" in ua:
                return "MacIntel"
            elif "Linux" in ua:
                return "Linux x86_64"
        return "Win32"

    def configure_browser(self, chrome_options) -> None:
        """Apply fingerprint settings to Chrome options before browser creation."""
        if not self.enabled:
            return

        fp = self.generate_fingerprint()

        # User agent
        if fp["user_agent"]:
            chrome_options.add_argument(f"--user-agent={fp['user_agent']}")

        # Resolution
        if fp["resolution"]:
            w, h = fp["resolution"]
            chrome_options.add_argument(f"--window-size={w},{h}")

        # Timezone
        chrome_options.add_argument(f"--timezone={fp['timezone']}")

        # Language
        if fp["languages"]:
            chrome_options.add_argument(f"--lang={fp['languages'][0]}")

        # Disable WebGL fingerprinting tells
        chrome_options.add_argument("--disable-reading-from-canvas")

    def apply_runtime_spoofing(self, driver):
        """Apply JavaScript-level fingerprint spoofing after browser starts."""
        if not self.enabled or not self.current_fingerprint:
            return

        fp = self.current_fingerprint
        scripts = []

        # Navigator properties
        scripts.append(f"""
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {fp['hardware_concurrency']}
            }});
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {fp['device_memory']}
            }});
        """)

        if fp.get("languages"):
            langs = fp["languages"]
            lang_js = str(langs).replace("'", '"')
            scripts.append(f"""
                Object.defineProperty(navigator, 'languages', {{
                    get: () => {lang_js}
                }});
                Object.defineProperty(navigator, 'language', {{
                    get: () => "{langs[0]}"
                }});
            """)

        # Platform
        if fp.get("platform"):
            scripts.append(f"""
                Object.defineProperty(navigator, 'platform', {{
                    get: () => "{fp['platform']}"
                }});
            """)

        # WebGL renderer spoofing
        if fp.get("webgl"):
            vendor, renderer = fp["webgl"]
            scripts.append(f"""
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return "{vendor}";
                    if (param === 37446) return "{renderer}";
                    return getParameter.apply(this, arguments);
                }};
            """)

        # Canvas noise injection
        if fp.get("canvas_noise", 0) > 0:
            noise = fp["canvas_noise"]
            scripts.append(f"""
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {{
                    const ctx = this.getContext('2d');
                    if (ctx) {{
                        const imageData = ctx.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imageData.data.length; i += 4) {{
                            imageData.data[i] += Math.floor(Math.random() * {noise} * 255);
                        }}
                        ctx.putImageData(imageData, 0, 0);
                    }}
                    return origToDataURL.apply(this, arguments);
                }};
            """)

        # Screen dimensions
        if fp.get("resolution"):
            w, h = fp["resolution"]
            scripts.append(f"""
                Object.defineProperty(screen, 'width', {{ get: () => {w} }});
                Object.defineProperty(screen, 'height', {{ get: () => {h} }});
                Object.defineProperty(screen, 'availWidth', {{ get: () => {w} }});
                Object.defineProperty(screen, 'availHeight', {{ get: () => {h - 40} }});
            """)

        # Execute all scripts
        combined = "\n".join(scripts)
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": combined
            })
            log.debug("Runtime fingerprint spoofing applied")
        except Exception as e:
            # Fallback: execute directly (less reliable but works)
            try:
                driver.execute_script(combined)
            except Exception as e2:
                log.debug(f"Fingerprint spoofing failed: {e2}")

    def get_fingerprint_info(self) -> dict:
        """Return current fingerprint for debugging."""
        if not self.current_fingerprint:
            return {"status": "not generated"}
        fp = self.current_fingerprint
        return {
            "user_agent": (fp.get("user_agent") or "default")[:60] + "...",
            "resolution": fp.get("resolution"),
            "timezone": fp.get("timezone"),
            "language": fp.get("languages", [None])[0],
            "cores": fp.get("hardware_concurrency"),
            "memory_gb": fp.get("device_memory"),
            "platform": fp.get("platform"),
            "webgl_vendor": fp.get("webgl", (None, None))[0],
            "canvas_noise": fp.get("canvas_noise", 0) > 0,
        }
