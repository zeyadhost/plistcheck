"""Run directly to curl every source URL emitted by the hardware checks."""
import ast
import subprocess
from pathlib import Path

import amd
from checker import DESKTOP_CPUS, DOCS, GUIDE, LAPTOP_GUIDES


def main():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "checker.py").read_text())
    links = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check":
            links.extend([node.args[7]] if len(node.args) > 7 else [])
            links.extend(k.value for k in node.keywords if k.arg == "link")
    urls = set()
    desktop_pages = ["AMD/zen.html" if cpu == "zen" else f"config.plist/{'haswell' if cpu == 'broadwell' else cpu}.html"
                     for cpu in DESKTOP_CPUS]
    profiles = [(GUIDE + page, False) for page in desktop_pages]
    profiles += [(GUIDE + f"config-laptop.plist/{page}.html", True) for page in set(LAPTOP_GUIDES.values())]
    for source, laptop in profiles:
        urls.add(source)
        env = {"GUIDE": GUIDE, "DOCS": DOCS, "amd": amd, "source": source, "gpu_source": "https://dortania.github.io/GPU-Buyers-Guide/",
               "laptop": laptop, "laptop_guide": laptop, "laptop_acpi": source + "#add" if laptop else "https://dortania.github.io/Getting-Started-With-ACPI/",
               "hardware_incomplete": False, "modern_intel": not source.endswith("/zen.html")}
        for link in links:
            url = eval(compile(ast.Expression(link), "<source link>", "eval"), {"__builtins__": {}}, env)
            if url.startswith("https://dortania.github.io/"):
                urls.add(url)
    failed = False
    for url in sorted(urls):
        code = subprocess.check_output(["curl", "-Ls", "--max-time", "30", "-o", "/dev/null", "-w", "%{http_code}", url], text=True)
        print(code, url)
        failed |= code != "200"
    raise SystemExit(int(failed))


if __name__ == "__main__":
    main()
