"""AI-dependent tests: need a local Ollama with the default model pulled.
Skipped automatically when Ollama isn't reachable."""

import asyncio
import os

os.environ["QUANTLAB_AUTOSAVE"] = "0"

from conftest import requires_ollama  # noqa: E402

DEMO = "demo_quant_research.ipynb"


async def _boot(app):
    from core import kernel_service
    for _ in range(120):
        if kernel_service.is_alive():
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("kernel did not start")


async def _wait_reply(app, baseline, timeout_s=300):
    for _ in range(timeout_s * 2):
        await asyncio.sleep(0.5)
        if (len(app.chat_history) >= baseline + 2
                and app.chat_history[-1]["role"] == "assistant"
                and not app.chat_busy):
            return app.chat_history[-1]["content"]
    raise TimeoutError("no assistant reply")


@requires_ollama
def test_chat_reads_live_notebook():
    from tui import QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)):
            await _boot(app)
            app.chat_history = []
            app.send_chat("Which random seed does this notebook use for the GBM "
                          "simulation? Answer with just the number.")
            reply = await _wait_reply(app, 0)
            assert "42" in reply  # only knowable by reading the notebook

    asyncio.run(run())


@requires_ollama
def test_chat_busy_reentry_guard():
    from tui import QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)):
            await _boot(app)
            app.chat_history = []
            app.send_chat("Reply with exactly: FIRST")
            await asyncio.sleep(0.3)
            assert app.chat_busy
            n = len(app.chat_history)
            app.send_chat("must be refused")
            await asyncio.sleep(0.3)
            assert len(app.chat_history) == n  # refused, not queued/cancelled
            await _wait_reply(app, 0)
            assert not app.chat_busy  # busy flag reset — chat still usable

    asyncio.run(run())


@requires_ollama
def test_agent_loop_achieves_simple_goal():
    from tui import QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)):
            await _boot(app)
            app.chat_history = []
            n_cells = len(app.cells)
            app.agent_loop("Create a variable agent_done = 21 * 2 and print it. "
                           "That is the entire goal; reply DONE once it prints 42.")
            for _ in range(2400):   # up to 20 min: cold model + multi-step loop
                await asyncio.sleep(0.5)
                if not app.chat_busy and not app.agent_active and app.chat_history:
                    break
            assert not app.agent_active
            assert len(app.cells) > n_cells  # it inserted and ran at least one cell
            all_out = " ".join(app._output_digest(w.cell) for w in app.cells)
            assert "42" in all_out

    asyncio.run(run())
