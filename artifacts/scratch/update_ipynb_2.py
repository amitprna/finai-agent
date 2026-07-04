import json
import os

path = r"c:\Users\kumar\M100\antigravity\genai-bootcamp\live-sessions\finai-agent\test\langfuse_tutorial.ipynb"
with open(path, "r", encoding="utf-8") as f:
    notebook = json.load(f)

updated = False
for cell in notebook["cells"]:
    if cell["cell_type"] == "code":
        source_str = "".join(cell["source"])
        if 'litellm.success_callback = ["langfuse"]' in source_str:
            # Replace with dynamic callback selection
            new_source = []
            for line in cell["source"]:
                if 'litellm.success_callback = ["langfuse"]' in line:
                    new_source.extend([
                        "# In langfuse v3+, we use 'langfuse_otel', in older versions 'langfuse'\n",
                        "from importlib.metadata import version\n",
                        "try:\n",
                        "    is_v3_or_higher = int(version(\"langfuse\").split(\".\")[0]) >= 3\n",
                        "    callback_name = \"langfuse_otel\" if is_v3_or_higher else \"langfuse\"\n",
                        "except Exception:\n",
                        "    callback_name = \"langfuse_otel\"\n",
                        "litellm.success_callback = [callback_name]\n"
                    ])
                else:
                    new_source.append(line)
            cell["source"] = new_source
            updated = True
            break

if updated:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notebook, f, indent=1, ensure_ascii=False)
    print("Successfully updated finai-agent/test/langfuse_tutorial.ipynb")
else:
    print("Could not find the target line to update.")
