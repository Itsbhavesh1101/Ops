import { execFileSync } from "child_process";
import * as path from "path";
import { fileURLToPath } from "url";

export default async function globalSetup(): Promise<void> {
  const setupDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(setupDir, "../../..");
  const python = process.env.E7_PYTHON_EXECUTABLE
    || (process.platform === "win32"
      ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
      : "python");

  execFileSync(python, ["scripts/bootstrap_acceptance_data.py"], {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONPATH: repoRoot,
    },
    stdio: "inherit",
  });
}
