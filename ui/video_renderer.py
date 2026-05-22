"""Server-side flythrough video renderer.

Renders the MapLibre flythrough page in a headless Chromium browser via
Playwright and captures the MP4 produced by the in-page WebCodecs encoder.
Works identically on localhost and on a remote server (no display needed).

One-time setup:
    pip install playwright
    playwright install chromium --with-deps
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# Windows' default SelectorEventLoop does not support subprocesses.
# Playwright needs ProactorEventLoop to launch the Chromium process.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from typing import List

# SwiftShader forces software WebGL — it works on GPU-less servers but maxes out the CPU
# on machines that have a GPU (Chromium bypasses hardware acceleration entirely).
# Set PLAYWRIGHT_SWIFTSHADER=1 in the environment only on headless servers without a GPU.
_USE_SWIFTSHADER = os.environ.get("PLAYWRIGHT_SWIFTSHADER", "").lower() in ("1", "true", "yes")


async def render_flythrough_async(
    track: List[List[float]],
    name: str,
    mode: str = "satellite_3d",
    duration_sec: int = 60,
    orientation: str = "landscape",
    resolution: str = "2K",
) -> bytes:
    """Render a flythrough MP4 via headless Chromium. Returns raw MP4 bytes.

    The page's WebCodecs encoder runs inside the real browser process, so
    quality is identical to a manual export.  SwiftShader provides software
    WebGL when no GPU is available (servers without a GPU still work).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && "
            "playwright install chromium --with-deps"
        ) from exc

    print(
        f"[ft-render] start  name={name!r}  dur={duration_sec}s  "
        f"res={resolution}  orient={orientation}  mode={mode}",
        flush=True,
    )
    t0 = time.time()

    from ui.flythrough_3d import _build_html

    html = _build_html(
        track, name,
        mode=mode,
        auto_export=True,          # triggers exportFull() automatically
        duration_sec=duration_sec,
        orientation=orientation,
        resolution=resolution,
    )

    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(html)
    tmp.close()

    try:
        async with async_playwright() as pw:
            chromium_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-renderer-backgrounding",
                # WebCodecs + hardware canvas
                "--enable-features=WebCodecs,MediaFoundationH264Encoding",
                "--enable-gpu-rasterization",
                "--enable-accelerated-2d-canvas",
                # Force Chromium to use the GPU even if it is on the blocklist
                "--ignore-gpu-blocklist",
                # On dual-GPU machines (laptop with iGPU + dGPU) prefer the dGPU
                "--force_high_performance_gpu",
                # D3D11 ANGLE backend — matches the Media Foundation H.264 encoder path on Windows
                "--use-gl=angle",
                "--use-angle=d3d11",
                # Remove the 60 Hz vsync cap in headless mode so rAF / render events
                # fire as fast as the GPU can handle (~5–8 ms/frame at 2K instead of 16 ms).
                "--disable-frame-rate-limit",
                "--disable-gpu-vsync",
            ]
            if _USE_SWIFTSHADER:
                # Software WebGL fallback for GPU-less servers — set PLAYWRIGHT_SWIFTSHADER=1
                chromium_args += ["--use-gl=angle", "--use-angle=swiftshader"]
            browser = await pw.chromium.launch(headless=True, args=chromium_args)
            ctx = await browser.new_context(
                accept_downloads=True,
                viewport={"width": 1280, "height": 720},
            )
            page = await ctx.new_page()

            # Forward every browser console.log / warn / error to the terminal.
            def _on_console(msg):
                tag = {"error": "ERR", "warning": "WRN"}.get(msg.type, "LOG")
                print(f"  [browser:{tag}] {msg.text}", flush=True)

            def _on_page_error(err):
                print(f"  [browser:PAGEERR] {err}", flush=True)

            page.on("console", _on_console)
            page.on("pageerror", _on_page_error)

            # Guarantee document.hidden == false so the pause-on-hidden guard
            # inside exportFull never activates in headless mode.
            await page.add_init_script(
                "Object.defineProperty(document,'hidden',{get:()=>false});"
                "Object.defineProperty(document,'visibilityState',{get:()=>'visible'});"
            )

            video_data: list[bytes] = []
            done = asyncio.Event()

            async def _on_download(dl):
                path = await dl.path()
                with open(path, "rb") as fh:
                    data = fh.read()
                video_data.append(data)
                elapsed = time.time() - t0
                print(
                    f"[ft-render] download received  size={len(data)/1e6:.1f} MB  "
                    f"elapsed={elapsed:.1f}s",
                    flush=True,
                )
                done.set()

            page.on("download", _on_download)

            # file:// URL so CDN scripts still load via the page's own network
            await page.goto(Path(tmp.name).as_uri(), wait_until="load")

            # Timeout budget:
            #   Hardware (NVENC) at 60 fps: ~0.04 s/frame × duration_sec×60 = duration_sec×2.4
            #     + 10 s prewarm (cached) → well within base budget.
            #   Software fallback (x264) at 15 fps: ~0.65 s/frame × duration_sec×15 frames
            #     + 65 s prewarm ≈ duration_sec×10 + 65 s.
            #   Use duration_sec×6 + 400 s which covers the software 15-fps path up to 60 s videos.
            #   SwiftShader (CPU-only software GL) is far slower — add a large extra buffer.
            timeout_s = duration_sec * 6 + 400 + (duration_sec * 10 if _USE_SWIFTSHADER else 0)
            print(f"[ft-render] page loaded — timeout budget: {timeout_s}s", flush=True)
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                elapsed = time.time() - t0
                raise TimeoutError(
                    f"Render timed out after {timeout_s}s  "
                    f"(video duration={duration_sec}s, elapsed={elapsed:.1f}s)"
                )
            finally:
                await browser.close()

        return video_data[0]

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def render_flythrough(
    track: List[List[float]],
    name: str,
    mode: str = "satellite_3d",
    duration_sec: int = 60,
    orientation: str = "landscape",
    resolution: str = "2K",
) -> bytes:
    """Synchronous wrapper — safe to call from any thread or coroutine.

    From a plain thread (Streamlit, Telegram handler):
        video = render_flythrough(track, name, ...)

    From an async context (aiogram, FastAPI):
        video = await render_flythrough_async(track, name, ...)
    """
    coro = render_flythrough_async(
        track, name,
        mode=mode,
        duration_sec=duration_sec,
        orientation=orientation,
        resolution=resolution,
    )
    try:
        asyncio.get_running_loop()
        # Running inside an event loop (e.g. Telegram bot) — use a fresh thread
        # with its own event loop to avoid deadlock.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop — safe to call asyncio.run directly
        return asyncio.run(coro)
