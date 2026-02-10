"""
E2E Agent Task Tests.

Tests that run actual agent tasks and validate the artifacts produced.
Uses pre-built test users (ag3ntum_tester_a) created by entrypoint-test.sh.

Tests cover:
- Shell script creation and execution
- Image generation via Python
- Web fetching and summarization

Prerequisites:
- Container must be running with test entrypoint (./run.sh test)
- API must be accessible at localhost:40080

Run these tests:
    ./run.sh test --only-e2e
    ./run.sh test --all
"""
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from tests.constants import (
    PREBUILT_USER_A_USERNAME as TEST_USER_USERNAME,
    PREBUILT_USER_A_EMAIL as TEST_USER_EMAIL,
    PREBUILT_USER_A_PASSWORD as TEST_USER_PASSWORD,
    PREBUILT_USER_A_UID as TEST_USER_UID,
)
from tests.backend.e2e_helpers import (
    API_V1_URL,
    is_docker_environment,
    api_accessible,
    run_agent_task,
    get_response_text,
)

# Longer timeout for complex agent tasks
AGENT_TIMEOUT = 180

TEST_USER_PERSISTENT_DIR = Path(f"/users/{TEST_USER_USERNAME}/ag3ntum/persistent")


# =============================================================================
# Module-Scoped Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def check_environment():
    """Skip all tests if not in proper environment."""
    if not is_docker_environment():
        pytest.skip("Agent E2E tests require Docker environment")
    if not api_accessible():
        pytest.skip("API not accessible at localhost:40080")


@pytest.fixture(scope="module")
def test_user(check_environment) -> dict:
    """Return credentials for the pre-built test user."""
    if not TEST_USER_PERSISTENT_DIR.exists():
        pytest.skip(
            f"Test user persistent dir not found: {TEST_USER_PERSISTENT_DIR}. "
            "Run with test entrypoint (./run.sh test) to create test users."
        )

    yield {
        "username": TEST_USER_USERNAME,
        "email": TEST_USER_EMAIL,
        "password": TEST_USER_PASSWORD,
        "uid": TEST_USER_UID,
        "persistent_dir": TEST_USER_PERSISTENT_DIR,
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def auth_token(test_user: dict) -> str:
    """Get JWT token for pre-built test user."""
    async with httpx.AsyncClient(base_url=API_V1_URL, timeout=30.0) as client:
        response = await client.post(
            "/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        if response.status_code != 200:
            pytest.fail(
                f"Failed to login as {test_user['email']}: "
                f"{response.status_code} - {response.text}. "
                "Ensure test users have DB entries (entrypoint-test.sh)."
            )
        token = response.json()["access_token"]
        print(f"[SETUP] Got auth token for {test_user['email']}")
        return token


# =============================================================================
# Test Class: Agent Task Execution
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestAgentTasks:
    """Tests that run agent tasks and validate produced artifacts."""

    @pytest.mark.asyncio
    async def test_create_and_run_bash_script(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent creates a bash script, runs it, and saves output."""
        print("\n[TEST] Create and run bash script")

        script_name = f"_test_list_dirs_{uuid.uuid4().hex[:6]}.sh"
        output_name = f"_test_list_dirs_output_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"Create a bash script at ./persistent/{script_name} that lists the "
            f"root directory / recursively up to 3 levels deep. The script should "
            f"use 'find / -maxdepth 3 -type d 2>/dev/null | head -50' and start with "
            f"#!/bin/bash. After creating it, make it executable with chmod +x, run it, "
            f"and save the output to ./persistent/{output_name}.",
            timeout=AGENT_TIMEOUT,
        )

        assert result["status"] == "agent_complete", (
            f"Task did not complete: {result.get('error')}"
        )

        # Validate script file
        script_path = test_user["persistent_dir"] / script_name
        assert script_path.exists(), f"Script {script_name} was not created"
        script_content = script_path.read_text()
        assert "#!/bin/bash" in script_content, "Script missing shebang"
        assert "find" in script_content, "Script doesn't contain find command"

        # Validate output file
        output_path = test_user["persistent_dir"] / output_name
        assert output_path.exists(), f"Output {output_name} was not created"
        output_content = output_path.read_text()
        assert len(output_content) > 10, "Output file is too small"
        assert any(
            d in output_content for d in ["/usr", "/etc", "/var", "/tmp"]
        ), f"Output doesn't contain expected directories: {output_content[:200]}"

        print(f"    Script created: {script_name} ({len(script_content)} bytes)")
        print(f"    Output created: {output_name} ({len(output_content)} bytes)")

        # Cleanup
        for p in [script_path, output_path]:
            try:
                p.unlink()
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_generate_cat_image(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent generates an image of a cat face."""
        print("\n[TEST] Generate cat image")

        marker = uuid.uuid4().hex[:6]
        png_name = f"_test_cat_{marker}.png"
        svg_name = f"_test_cat_{marker}.svg"

        result = await run_agent_task(
            auth_token,
            f"Write a Python script and run it to generate a simple image of a cute "
            f"cat face using basic shapes (circles for eyes, triangle for nose, curves "
            f"for mouth and whiskers). Save the image as ./persistent/{png_name}. "
            f"Use PIL/Pillow if available. If PIL is not available, create an SVG file "
            f"at ./persistent/{svg_name} instead using raw XML string writing. "
            f"The image must be at least 100x100 pixels or equivalent.",
            timeout=AGENT_TIMEOUT,
        )

        assert result["status"] == "agent_complete", (
            f"Task did not complete: {result.get('error')}"
        )

        # Validate — either PNG or SVG should exist
        png_path = test_user["persistent_dir"] / png_name
        svg_path = test_user["persistent_dir"] / svg_name

        image_path = png_path if png_path.exists() else svg_path if svg_path.exists() else None

        assert image_path is not None, (
            f"Neither {png_name} nor {svg_name} was created in persistent dir"
        )

        file_size = image_path.stat().st_size
        assert file_size > 100, (
            f"Image file is too small ({file_size} bytes), likely empty or truncated"
        )

        print(f"    Image created: {image_path.name} ({file_size} bytes)")

        # Cleanup
        for p in [png_path, svg_path]:
            try:
                p.unlink()
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_fetch_website_and_summarize(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent fetches a website and writes a summary."""
        print("\n[TEST] Fetch website and summarize")

        summary_name = f"_test_extractum_summary_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"Fetch the website https://extractum.io using mcp__ag3ntum__WebFetch "
            f"and write a summary of what the company does. Save the summary to "
            f"./persistent/{summary_name}. The summary should be at least 50 "
            f"characters long and describe what Extractum does.",
            timeout=AGENT_TIMEOUT,
        )

        assert result["status"] == "agent_complete", (
            f"Task did not complete: {result.get('error')}"
        )

        # Validate summary file
        summary_path = test_user["persistent_dir"] / summary_name
        assert summary_path.exists(), f"Summary {summary_name} was not created"

        content = summary_path.read_text()
        assert len(content) >= 50, (
            f"Summary too short ({len(content)} chars): {content}"
        )

        # Check for relevant keywords
        content_lower = content.lower()
        relevant_keywords = ["extract", "data", "ai", "web", "scraping", "content", "information"]
        assert any(kw in content_lower for kw in relevant_keywords), (
            f"Summary doesn't contain relevant keywords: {content[:200]}"
        )

        print(f"    Summary created: {summary_name} ({len(content)} chars)")
        print(f"    Preview: {content[:100]}...")

        # Cleanup
        try:
            summary_path.unlink()
        except OSError:
            pass
