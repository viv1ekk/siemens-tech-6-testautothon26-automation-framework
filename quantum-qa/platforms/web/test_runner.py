"""
Test Runner - Executes generated Playwright tests and parses results.
"""

import json
import asyncio
from pathlib import Path


class PlaywrightTestRunner:
    """Runs generated Playwright test files and parses results."""
    
    @staticmethod
    async def run_test_file(test_file: Path) -> dict:
        """
        Execute a Playwright test file and return parsed results.
        
        Args:
            test_file: Path to test_*.spec.ts file
            
        Returns:
            {
                "passed": bool,
                "total_tests": int,
                "passed_tests": int,
                "failed_tests": int,
                "duration_s": float,
                "test_file": str,
                "details": dict
            }
        """
        import time
        start_time = time.time()
        
        try:
            # Check if test file exists
            if not test_file.exists():
                return {
                    "passed": False,
                    "error": f"Test file not found: {test_file}",
                    "duration_s": 0
                }
            
            output_file = test_file.parent.parent / "test-results.json"

            cmd = [
                "npx", "playwright", "test",
                str(test_file),
                "--reporter=json",
                "--timeout=30000"
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                proc.kill()
                return {
                    "passed": False,
                    "error": "Test execution timeout",
                    "duration_s": round(time.time() - start_time, 2)
                }

            duration = time.time() - start_time

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            reporter_text = stdout_text if stdout_text.startswith("{") else ""
            if not reporter_text and output_file.exists():
                reporter_text = output_file.read_text()

            if reporter_text:
                try:
                    results_json = json.loads(reporter_text)
                    stats = results_json.get("stats", {})

                    total = stats.get("expected", 0)
                    failed = stats.get("failed", 0)
                    skipped = stats.get("skipped", 0)
                    passed = total - failed - skipped

                    output_file.write_text(json.dumps(results_json, indent=2))
                    return {
                        "passed": failed == 0,
                        "total_tests": total,
                        "passed_tests": passed,
                        "failed_tests": failed,
                        "skipped_tests": skipped,
                        "duration_s": round(duration, 2),
                        "test_file": str(test_file),
                        "details": {
                            "stats": stats,
                            "exit_code": exit_code,
                            "stderr": stderr_text[:500],
                        }
                    }
                except json.JSONDecodeError:
                    pass

            return {
                "passed": exit_code == 0,
                "duration_s": round(duration, 2),
                "test_file": str(test_file),
                "exit_code": exit_code,
                "method": "exit_code_check",
                "error": stderr_text[:500],
            }
            
        except Exception as e:
            return {
                "passed": False,
                "error": str(e),
                "duration_s": round(time.time() - start_time, 2),
                "test_file": str(test_file)
            }
    
    @staticmethod
    def find_generated_tests(host: str = None, scenario: str = None) -> list:
        """
        Find generated test files.
        
        Args:
            host: Optional host name (e.g., "automationexercise_com")
            scenario: Optional scenario name (e.g., "ui_smoke")
            
        Returns:
            List of paths to test_*.spec.ts files
        """
        artifacts_dir = Path("artifacts/ui_generated")
        
        if host and scenario:
            pattern = f"{host}/{scenario}/tests/test_*.spec.ts"
            return list(artifacts_dir.glob(pattern))
        elif host:
            pattern = f"{host}/*/tests/test_*.spec.ts"
            return list(artifacts_dir.glob(pattern))
        else:
            pattern = "*/*/tests/test_*.spec.ts"
            return list(artifacts_dir.glob(pattern))
