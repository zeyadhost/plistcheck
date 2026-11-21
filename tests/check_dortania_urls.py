"""Run directly to curl every source URL emitted by the hardware checks."""
import ast
import subprocess
from pathlib import Path

from checker import DOCS, GENERATIONS, GUIDE


def main():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "checker.py").read_text())
    links = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check":
            links.extend([node.args[7]] if len(node.args) > 7 else [])
            links.extend(k.value for k in node.keywords if k.arg == "link")
    urls = set()
    for cpu in GENERATIONS:
        page = "haswell" if cpu == "broadwell" else "coffee-lake" if cpu == "other" else cpu
        source = GUIDE + ("AMD/zen.html" if cpu == "zen" else f"config.plist/{page}.html")
        urls.add(source)
        env = {"GUIDE": GUIDE, "DOCS": DOCS, "source": source, "gpu_source": "https://dortania.github.io/GPU-Buyers-Guide/"}
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
