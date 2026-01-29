#!/usr/bin/env python3
"""
Claude Code Security Environment Assessment Tool
================================================

A NON-INVASIVE security posture assessment tool that checks the current
environment for security risks when running Claude Code.

This script ONLY performs READ-ONLY operations:
- Checks file/directory existence and permissions
- Reads environment variables
- Checks process visibility (own process only)
- Tests network reachability (connect only, no data sent)

It NEVER:
- Writes to any files
- Modifies any system state
- Executes destructive commands
- Reads actual content of sensitive files

Usage:
    python claude-code-security-env-check.py [--json] [--verbose]

Author: Ag3ntum Security Team
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class Severity(Enum):
    """Risk severity levels matching the security comparison document."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    MITIGATED = "MITIGATED"
    INFO = "INFO"


class Category(Enum):
    """Security check categories from ag3ntum_vs_claude_code.md."""
    FILESYSTEM = "Filesystem Access Control"
    COMMAND = "Command Execution Security"
    PROCESS = "Process Information Disclosure"
    NETWORK = "Network Security"
    MULTITENANT = "Multi-Tenant Security"
    DOS = "Denial of Service"
    SECRETS = "Secrets and Credential Exposure"
    INJECTION = "Prompt Injection Vectors"
    PERSISTENCE = "Persistence and Backdoors"
    AUDIT = "Compliance and Audit"


@dataclass
class CheckResult:
    """Result of a single security check."""
    check_id: str
    name: str
    category: str
    severity: str
    status: str  # "VULNERABLE", "PROTECTED", "PARTIAL", "N/A"
    description: str
    details: str = ""
    recommendation: str = ""


@dataclass
class AssessmentReport:
    """Complete security assessment report."""
    timestamp: str = ""
    hostname: str = ""
    platform: str = ""
    python_version: str = ""
    user: str = ""
    uid: Optional[int] = None
    gid: Optional[int] = None
    home_dir: str = ""
    working_dir: str = ""
    is_docker: bool = False
    is_root: bool = False
    checks: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


class SecurityAssessment:
    """Non-invasive security posture assessment for Claude Code environments."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.report = AssessmentReport()
        self.checks: list[CheckResult] = []
        self._collect_system_info()

    def _collect_system_info(self) -> None:
        """Collect basic system information (non-invasive)."""
        self.report.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.report.hostname = platform.node()
        self.report.platform = f"{platform.system()} {platform.release()}"
        self.report.python_version = platform.python_version()
        self.report.working_dir = os.getcwd()
        self.report.home_dir = str(Path.home())

        # Get user info
        try:
            self.report.user = os.getlogin()
        except (OSError, AttributeError):
            self.report.user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))

        # Unix-specific
        if hasattr(os, "getuid"):
            self.report.uid = os.getuid()
            self.report.gid = os.getgid()
            self.report.is_root = os.getuid() == 0

        # Detect Docker
        self.report.is_docker = self._is_in_docker()

    def _is_in_docker(self) -> bool:
        """Check if running inside Docker container."""
        # Check /.dockerenv file
        if os.path.exists("/.dockerenv"):
            return True

        # Check cgroup for docker
        try:
            with open("/proc/1/cgroup", "r") as f:
                return "docker" in f.read() or "containerd" in f.read()
        except (FileNotFoundError, PermissionError):
            pass

        return False

    def _is_windows(self) -> bool:
        return platform.system() == "Windows"

    def _is_macos(self) -> bool:
        return platform.system() == "Darwin"

    def _is_linux(self) -> bool:
        return platform.system() == "Linux"

    def _path_exists(self, path: str) -> bool:
        """Check if path exists (handles home expansion)."""
        try:
            expanded = os.path.expanduser(path)
            return os.path.exists(expanded)
        except (OSError, ValueError):
            return False

    def _path_readable(self, path: str) -> bool:
        """Check if path is readable (without actually reading)."""
        try:
            expanded = os.path.expanduser(path)
            return os.access(expanded, os.R_OK)
        except (OSError, ValueError):
            return False

    def _path_writable(self, path: str) -> bool:
        """Check if path is writable (without actually writing)."""
        try:
            expanded = os.path.expanduser(path)
            return os.access(expanded, os.W_OK)
        except (OSError, ValueError):
            return False

    def _get_permissions(self, path: str) -> Optional[str]:
        """Get file permissions as octal string (Unix only)."""
        if self._is_windows():
            return None
        try:
            expanded = os.path.expanduser(path)
            mode = os.stat(expanded).st_mode
            return oct(mode)[-3:]
        except (OSError, ValueError):
            return None

    def _add_check(self, result: CheckResult) -> None:
        """Add a check result to the report."""
        self.checks.append(result)
        if self.verbose:
            status_symbol = {
                "VULNERABLE": "\u274c",
                "PROTECTED": "\u2705",
                "PARTIAL": "\u26a0\ufe0f",
                "N/A": "\u2796"
            }.get(result.status, "?")
            print(f"  {status_symbol} [{result.severity}] {result.name}: {result.status}")

    # =========================================================================
    # Category 1: Filesystem Access Control
    # =========================================================================

    def check_filesystem_access(self) -> None:
        """Check filesystem access control risks."""
        if self.verbose:
            print("\n[Category 1: Filesystem Access Control]")

        # Check 1.1: /etc/passwd readable
        self._add_check(CheckResult(
            check_id="FS-001",
            name="/etc/passwd accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.HIGH.value if self._path_readable("/etc/passwd") else Severity.MITIGATED.value,
            status="VULNERABLE" if self._path_readable("/etc/passwd") else "PROTECTED",
            description="Check if /etc/passwd is readable by the current process",
            details=f"Path exists: {self._path_exists('/etc/passwd')}, Readable: {self._path_readable('/etc/passwd')}",
            recommendation="In Ag3ntum, /etc is not mounted in the sandbox"
        ))

        # Check 1.2: /etc/shadow readable (should never be for non-root)
        shadow_readable = self._path_readable("/etc/shadow")
        self._add_check(CheckResult(
            check_id="FS-002",
            name="/etc/shadow accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.CRITICAL.value if shadow_readable else Severity.MITIGATED.value,
            status="VULNERABLE" if shadow_readable else "PROTECTED",
            description="Check if /etc/shadow is readable (password hashes)",
            details=f"Readable: {shadow_readable}",
            recommendation="Never run as root; /etc/shadow should not be readable"
        ))

        # Check 1.3: SSH keys accessible
        ssh_paths = [
            "~/.ssh/id_rsa",
            "~/.ssh/id_ed25519",
            "~/.ssh/id_ecdsa",
            "~/.ssh/id_dsa",
            "~/.ssh/authorized_keys"
        ]
        ssh_accessible = []
        for path in ssh_paths:
            if self._path_readable(path):
                ssh_accessible.append(path)

        self._add_check(CheckResult(
            check_id="FS-003",
            name="SSH key accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.CRITICAL.value if ssh_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if ssh_accessible else "PROTECTED",
            description="Check if SSH private keys are accessible",
            details=f"Accessible keys: {ssh_accessible}" if ssh_accessible else "No SSH keys accessible",
            recommendation="In Ag3ntum, ~/.ssh is not mounted in the sandbox"
        ))

        # Check 1.4: AWS credentials accessible
        aws_paths = [
            "~/.aws/credentials",
            "~/.aws/config"
        ]
        aws_accessible = [p for p in aws_paths if self._path_readable(p)]
        self._add_check(CheckResult(
            check_id="FS-004",
            name="AWS credentials accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.CRITICAL.value if aws_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if aws_accessible else "PROTECTED",
            description="Check if AWS credential files are accessible",
            details=f"Accessible: {aws_accessible}" if aws_accessible else "No AWS credentials accessible",
            recommendation="In Ag3ntum, ~/.aws is not mounted in the sandbox"
        ))

        # Check 1.5: Browser profile accessible
        browser_paths = [
            "~/.config/google-chrome/Default/Cookies",
            "~/.config/google-chrome/Default/Login Data",
            "~/.config/chromium/Default/Cookies",
            "~/Library/Application Support/Google/Chrome/Default/Cookies",  # macOS
            "~/.mozilla/firefox",
        ]
        browser_accessible = [p for p in browser_paths if self._path_exists(p)]
        self._add_check(CheckResult(
            check_id="FS-005",
            name="Browser profile accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.CRITICAL.value if browser_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if browser_accessible else "PROTECTED",
            description="Check if browser profiles (cookies, passwords) are accessible",
            details=f"Accessible: {browser_accessible}" if browser_accessible else "No browser profiles accessible",
            recommendation="In Ag3ntum, browser profiles are not mounted"
        ))

        # Check 1.6: .env files in common locations
        env_paths = [
            "./.env",
            "../.env",
            "../../.env",
            "~/.env"
        ]
        env_accessible = [p for p in env_paths if self._path_readable(p)]
        self._add_check(CheckResult(
            check_id="FS-006",
            name=".env file accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.HIGH.value if env_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if env_accessible else "PROTECTED",
            description="Check if .env files are accessible",
            details=f"Accessible: {env_accessible}" if env_accessible else "No .env files accessible in common paths",
            recommendation="Ag3ntum blocklists *.env pattern"
        ))

        # Check 1.7: .git directory accessible
        git_readable = self._path_readable(".git/config")
        self._add_check(CheckResult(
            check_id="FS-007",
            name=".git directory accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.HIGH.value if git_readable else Severity.MITIGATED.value,
            status="VULNERABLE" if git_readable else "PROTECTED",
            description="Check if .git/config is readable (may contain tokens)",
            details=f"Readable: {git_readable}",
            recommendation="Ag3ntum blocklists .git/** pattern"
        ))

        # Check 1.8: Parent directory traversal
        # Check if we can read files outside working directory
        parent_accessible = self._path_readable("../")
        grandparent_accessible = self._path_readable("../../")
        self._add_check(CheckResult(
            check_id="FS-008",
            name="Parent directory traversal",
            category=Category.FILESYSTEM.value,
            severity=Severity.HIGH.value if grandparent_accessible else Severity.MEDIUM.value if parent_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if grandparent_accessible else "PARTIAL" if parent_accessible else "PROTECTED",
            description="Check if parent directory traversal is possible",
            details=f"Parent accessible: {parent_accessible}, Grandparent accessible: {grandparent_accessible}",
            recommendation="Ag3ntum PathValidator blocks ../ traversal outside workspace"
        ))

        # Check 1.9: Kubernetes secrets (if in K8s)
        k8s_secrets_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        k8s_accessible = self._path_readable(k8s_secrets_path)
        self._add_check(CheckResult(
            check_id="FS-009",
            name="Kubernetes secrets accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.CRITICAL.value if k8s_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if k8s_accessible else "N/A" if not self._path_exists("/var/run/secrets") else "PROTECTED",
            description="Check if Kubernetes service account token is accessible",
            details=f"Accessible: {k8s_accessible}",
            recommendation="In Ag3ntum, /var/run/secrets is not mounted"
        ))

        # Check 1.10: npm/yarn tokens
        npm_paths = ["~/.npmrc", "~/.yarnrc"]
        npm_accessible = [p for p in npm_paths if self._path_readable(p)]
        self._add_check(CheckResult(
            check_id="FS-010",
            name="NPM/Yarn token accessibility",
            category=Category.FILESYSTEM.value,
            severity=Severity.HIGH.value if npm_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if npm_accessible else "PROTECTED",
            description="Check if npm/yarn configuration with tokens is accessible",
            details=f"Accessible: {npm_accessible}" if npm_accessible else "No npm/yarn configs accessible",
            recommendation="In Ag3ntum, home directory is not mounted"
        ))

    # =========================================================================
    # Category 2: Command Execution Security
    # =========================================================================

    def check_command_execution(self) -> None:
        """Check command execution risks."""
        if self.verbose:
            print("\n[Category 2: Command Execution Security]")

        # Check 2.1: Running as root
        is_root = self.report.is_root
        self._add_check(CheckResult(
            check_id="CMD-001",
            name="Running as root",
            category=Category.COMMAND.value,
            severity=Severity.CRITICAL.value if is_root else Severity.MITIGATED.value,
            status="VULNERABLE" if is_root else "PROTECTED",
            description="Check if current process is running as root (UID 0)",
            details=f"UID: {self.report.uid}, Running as root: {is_root}",
            recommendation="Never run Claude Code as root"
        ))

        # Check 2.2: sudo available (Unix only)
        if not self._is_windows():
            sudo_exists = self._path_exists("/usr/bin/sudo") or self._path_exists("/bin/sudo")
            # Try to check if sudo works without password (non-invasive: sudo -n true)
            sudo_nopasswd = False
            try:
                result = subprocess.run(
                    ["sudo", "-n", "true"],
                    capture_output=True,
                    timeout=2
                )
                sudo_nopasswd = result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
                pass

            self._add_check(CheckResult(
                check_id="CMD-002",
                name="sudo availability",
                category=Category.COMMAND.value,
                severity=Severity.CRITICAL.value if sudo_nopasswd else Severity.MEDIUM.value if sudo_exists else Severity.MITIGATED.value,
                status="VULNERABLE" if sudo_nopasswd else "PARTIAL" if sudo_exists else "PROTECTED",
                description="Check if sudo is available and if NOPASSWD is configured",
                details=f"sudo exists: {sudo_exists}, NOPASSWD: {sudo_nopasswd}",
                recommendation="Ag3ntum command filter blocks sudo commands"
            ))

        # Check 2.3: Docker socket accessible
        docker_sock_paths = [
            "/var/run/docker.sock",
            "/run/docker.sock"
        ]
        docker_accessible = any(self._path_exists(p) and self._path_readable(p) for p in docker_sock_paths)
        self._add_check(CheckResult(
            check_id="CMD-003",
            name="Docker socket accessibility",
            category=Category.COMMAND.value,
            severity=Severity.CRITICAL.value if docker_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if docker_accessible else "PROTECTED",
            description="Check if Docker socket is accessible (container escape risk)",
            details=f"Docker socket accessible: {docker_accessible}",
            recommendation="Never mount Docker socket into containers with AI agents"
        ))

        # Check 2.4: cron access
        cron_paths = [
            "/var/spool/cron/crontabs/",
            "/etc/cron.d/",
            "/etc/crontab"
        ]
        cron_writable = any(self._path_writable(p) for p in cron_paths)
        cron_readable = any(self._path_readable(p) for p in cron_paths)
        self._add_check(CheckResult(
            check_id="CMD-004",
            name="Cron job access",
            category=Category.COMMAND.value,
            severity=Severity.HIGH.value if cron_writable else Severity.LOW.value if cron_readable else Severity.MITIGATED.value,
            status="VULNERABLE" if cron_writable else "PARTIAL" if cron_readable else "PROTECTED",
            description="Check if cron directories are accessible",
            details=f"Writable: {cron_writable}, Readable: {cron_readable}",
            recommendation="In Ag3ntum, cron paths are not accessible in sandbox"
        ))

        # Check 2.5: systemd access
        systemd_paths = [
            "~/.config/systemd/user/",
            "/etc/systemd/system/"
        ]
        systemd_writable = any(self._path_writable(os.path.expanduser(p)) for p in systemd_paths)
        self._add_check(CheckResult(
            check_id="CMD-005",
            name="Systemd service access",
            category=Category.COMMAND.value,
            severity=Severity.HIGH.value if systemd_writable else Severity.MITIGATED.value,
            status="VULNERABLE" if systemd_writable else "PROTECTED",
            description="Check if systemd service directories are writable",
            details=f"Writable: {systemd_writable}",
            recommendation="In Ag3ntum, systemd paths are not accessible"
        ))

    # =========================================================================
    # Category 3: Process Information Disclosure
    # =========================================================================

    def check_process_disclosure(self) -> None:
        """Check process information disclosure risks."""
        if self.verbose:
            print("\n[Category 3: Process Information Disclosure]")

        if self._is_windows():
            # Windows process checks would be different
            self._add_check(CheckResult(
                check_id="PROC-001",
                name="Process enumeration (Windows)",
                category=Category.PROCESS.value,
                severity=Severity.INFO.value,
                status="N/A",
                description="Windows process enumeration check not implemented",
                details="Windows uses different APIs for process enumeration"
            ))
            return

        # Check 3.1: /proc accessible
        proc_accessible = self._path_exists("/proc") and self._path_readable("/proc")
        self._add_check(CheckResult(
            check_id="PROC-001",
            name="/proc filesystem accessibility",
            category=Category.PROCESS.value,
            severity=Severity.HIGH.value if proc_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if proc_accessible else "PROTECTED",
            description="Check if /proc filesystem is accessible",
            details=f"/proc accessible: {proc_accessible}",
            recommendation="Ag3ntum filters /proc to only expose /proc/self and safe entries"
        ))

        # Check 3.2: Can enumerate other PIDs
        can_enum_pids = False
        visible_pids = []
        try:
            if os.path.exists("/proc"):
                for entry in os.listdir("/proc"):
                    if entry.isdigit() and entry != str(os.getpid()):
                        if self._path_exists(f"/proc/{entry}/cmdline"):
                            can_enum_pids = True
                            visible_pids.append(entry)
                            if len(visible_pids) >= 5:  # Sample only
                                break
        except (PermissionError, OSError):
            pass

        self._add_check(CheckResult(
            check_id="PROC-002",
            name="Process enumeration",
            category=Category.PROCESS.value,
            severity=Severity.CRITICAL.value if can_enum_pids else Severity.MITIGATED.value,
            status="VULNERABLE" if can_enum_pids else "PROTECTED",
            description="Check if other process PIDs are enumerable",
            details=f"Can enumerate PIDs: {can_enum_pids}, Sample PIDs: {visible_pids[:5]}",
            recommendation="Ag3ntum uses --tmpfs /proc with selective bind-mounts"
        ))

        # Check 3.3: Can read other process environments
        can_read_env = False
        if can_enum_pids and visible_pids:
            for pid in visible_pids[:3]:
                env_path = f"/proc/{pid}/environ"
                if self._path_readable(env_path):
                    can_read_env = True
                    break

        self._add_check(CheckResult(
            check_id="PROC-003",
            name="Process environment leakage",
            category=Category.PROCESS.value,
            severity=Severity.CRITICAL.value if can_read_env else Severity.MITIGATED.value,
            status="VULNERABLE" if can_read_env else "PROTECTED",
            description="Check if other process environment variables are readable (may contain secrets)",
            details=f"Can read process environ: {can_read_env}",
            recommendation="Ag3ntum does not mount /proc/[pid]/ directories"
        ))

        # Check 3.4: Can read own environment (should be minimal in sandbox)
        own_env_count = len(os.environ)
        sensitive_env_vars = []
        sensitive_patterns = ["KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "AUTH", "API"]
        for key in os.environ:
            for pattern in sensitive_patterns:
                if pattern in key.upper():
                    sensitive_env_vars.append(key)
                    break

        self._add_check(CheckResult(
            check_id="PROC-004",
            name="Environment variable exposure",
            category=Category.PROCESS.value,
            severity=Severity.HIGH.value if sensitive_env_vars else Severity.LOW.value if own_env_count > 20 else Severity.MITIGATED.value,
            status="VULNERABLE" if sensitive_env_vars else "PARTIAL" if own_env_count > 20 else "PROTECTED",
            description="Check how many environment variables are exposed to the process",
            details=f"Total env vars: {own_env_count}, Potentially sensitive: {sensitive_env_vars}",
            recommendation="Ag3ntum uses --clearenv and only sets HOME and PATH"
        ))

    # =========================================================================
    # Category 4: Network Security
    # =========================================================================

    def check_network_security(self) -> None:
        """Check network security risks."""
        if self.verbose:
            print("\n[Category 4: Network Security]")

        # Check 4.1: Cloud metadata endpoint accessible
        cloud_metadata_accessible = False
        metadata_endpoints = [
            ("169.254.169.254", 80, "AWS/GCP metadata"),
            ("169.254.170.2", 80, "ECS metadata"),
        ]

        for host, port, name in metadata_endpoints:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    cloud_metadata_accessible = True
                    break
            except (socket.error, OSError):
                pass

        self._add_check(CheckResult(
            check_id="NET-001",
            name="Cloud metadata endpoint accessibility",
            category=Category.NETWORK.value,
            severity=Severity.HIGH.value if cloud_metadata_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if cloud_metadata_accessible else "PROTECTED",
            description="Check if cloud metadata endpoints are reachable (IAM credential theft)",
            details=f"Metadata accessible: {cloud_metadata_accessible}",
            recommendation="Ag3ntum blocks 169.254.169.254 in domain blocklist"
        ))

        # Check 4.2: Localhost services accessible
        localhost_ports = [
            (6379, "Redis"),
            (5432, "PostgreSQL"),
            (3306, "MySQL"),
            (27017, "MongoDB"),
            (8500, "Consul"),
            (2379, "etcd"),
        ]
        accessible_services = []

        for port, name in localhost_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                if result == 0:
                    accessible_services.append(f"{name}:{port}")
            except (socket.error, OSError):
                pass

        self._add_check(CheckResult(
            check_id="NET-002",
            name="Localhost service accessibility",
            category=Category.NETWORK.value,
            severity=Severity.HIGH.value if accessible_services else Severity.MITIGATED.value,
            status="VULNERABLE" if accessible_services else "PROTECTED",
            description="Check if localhost services are reachable",
            details=f"Accessible services: {accessible_services}" if accessible_services else "No common services accessible",
            recommendation="Ag3ntum can enable network sandboxing to isolate network"
        ))

        # Check 4.3: External network access
        external_accessible = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            # Try to reach a known reliable IP (Google DNS)
            result = sock.connect_ex(("8.8.8.8", 53))
            sock.close()
            external_accessible = result == 0
        except (socket.error, OSError):
            pass

        self._add_check(CheckResult(
            check_id="NET-003",
            name="External network access",
            category=Category.NETWORK.value,
            severity=Severity.MEDIUM.value if external_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if external_accessible else "PROTECTED",
            description="Check if external network is accessible (data exfiltration risk)",
            details=f"External network accessible: {external_accessible}",
            recommendation="Enable network sandboxing if agents don't need network access"
        ))

    # =========================================================================
    # Category 5: Secrets and Credential Exposure
    # =========================================================================

    def check_secrets_exposure(self) -> None:
        """Check secrets and credential exposure risks."""
        if self.verbose:
            print("\n[Category 5: Secrets and Credential Exposure]")

        # Check 5.1: Git credentials
        git_cred_paths = [
            "~/.git-credentials",
            "~/.gitconfig"
        ]
        git_creds_accessible = [p for p in git_cred_paths if self._path_readable(p)]
        self._add_check(CheckResult(
            check_id="SEC-001",
            name="Git credentials accessibility",
            category=Category.SECRETS.value,
            severity=Severity.HIGH.value if git_creds_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if git_creds_accessible else "PROTECTED",
            description="Check if git credential files are accessible",
            details=f"Accessible: {git_creds_accessible}" if git_creds_accessible else "No git credentials accessible",
            recommendation="Ag3ntum does not mount home directory credential files"
        ))

        # Check 5.2: Docker credentials
        docker_config = os.path.expanduser("~/.docker/config.json")
        docker_creds_accessible = self._path_readable(docker_config)
        self._add_check(CheckResult(
            check_id="SEC-002",
            name="Docker credentials accessibility",
            category=Category.SECRETS.value,
            severity=Severity.HIGH.value if docker_creds_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if docker_creds_accessible else "PROTECTED",
            description="Check if Docker registry credentials are accessible",
            details=f"~/.docker/config.json accessible: {docker_creds_accessible}",
            recommendation="Ag3ntum does not mount ~/.docker"
        ))

        # Check 5.3: GPG/PGP keys
        gpg_paths = ["~/.gnupg/", "~/.gpg/"]
        gpg_accessible = any(self._path_exists(p) and self._path_readable(p) for p in gpg_paths)
        self._add_check(CheckResult(
            check_id="SEC-003",
            name="GPG/PGP key accessibility",
            category=Category.SECRETS.value,
            severity=Severity.HIGH.value if gpg_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if gpg_accessible else "PROTECTED",
            description="Check if GPG/PGP private keys are accessible",
            details=f"GPG directory accessible: {gpg_accessible}",
            recommendation="Ag3ntum does not mount GPG directories"
        ))

        # Check 5.4: Cloud provider configs
        cloud_configs = [
            ("~/.config/gcloud/", "GCP"),
            ("~/.azure/", "Azure"),
            ("~/.aliyun/", "Alibaba Cloud"),
            ("~/.oci/", "Oracle Cloud"),
        ]
        accessible_clouds = [(path, name) for path, name in cloud_configs
                             if self._path_exists(path) and self._path_readable(path)]

        self._add_check(CheckResult(
            check_id="SEC-004",
            name="Cloud provider config accessibility",
            category=Category.SECRETS.value,
            severity=Severity.CRITICAL.value if accessible_clouds else Severity.MITIGATED.value,
            status="VULNERABLE" if accessible_clouds else "PROTECTED",
            description="Check if cloud provider credential directories are accessible",
            details=f"Accessible: {[name for _, name in accessible_clouds]}" if accessible_clouds else "No cloud configs accessible",
            recommendation="Ag3ntum does not mount cloud config directories"
        ))

        # Check 5.5: Password manager databases
        password_dbs = [
            "~/.password-store/",
            "~/.local/share/keyrings/",
            "~/Library/Keychains/",  # macOS
        ]
        pw_accessible = [p for p in password_dbs if self._path_exists(p)]
        self._add_check(CheckResult(
            check_id="SEC-005",
            name="Password manager accessibility",
            category=Category.SECRETS.value,
            severity=Severity.CRITICAL.value if pw_accessible else Severity.MITIGATED.value,
            status="VULNERABLE" if pw_accessible else "PROTECTED",
            description="Check if password manager databases are accessible",
            details=f"Accessible: {pw_accessible}" if pw_accessible else "No password managers accessible",
            recommendation="Ag3ntum does not mount keychain/password directories"
        ))

    # =========================================================================
    # Category 6: Persistence and Backdoors
    # =========================================================================

    def check_persistence(self) -> None:
        """Check persistence and backdoor risks."""
        if self.verbose:
            print("\n[Category 6: Persistence and Backdoors]")

        # Check 6.1: SSH authorized_keys writable
        auth_keys = os.path.expanduser("~/.ssh/authorized_keys")
        auth_keys_writable = self._path_writable(auth_keys) if self._path_exists(auth_keys) else self._path_writable(os.path.expanduser("~/.ssh"))
        self._add_check(CheckResult(
            check_id="PERS-001",
            name="SSH authorized_keys writability",
            category=Category.PERSISTENCE.value,
            severity=Severity.CRITICAL.value if auth_keys_writable else Severity.MITIGATED.value,
            status="VULNERABLE" if auth_keys_writable else "PROTECTED",
            description="Check if SSH authorized_keys can be modified (permanent SSH access)",
            details=f"Writable: {auth_keys_writable}",
            recommendation="Ag3ntum does not mount ~/.ssh"
        ))

        # Check 6.2: Shell profile writable
        shell_profiles = [
            "~/.bashrc",
            "~/.bash_profile",
            "~/.zshrc",
            "~/.profile",
        ]
        writable_profiles = [p for p in shell_profiles if self._path_writable(p)]
        self._add_check(CheckResult(
            check_id="PERS-002",
            name="Shell profile writability",
            category=Category.PERSISTENCE.value,
            severity=Severity.HIGH.value if writable_profiles else Severity.MITIGATED.value,
            status="VULNERABLE" if writable_profiles else "PROTECTED",
            description="Check if shell profiles can be modified (code execution on login)",
            details=f"Writable profiles: {writable_profiles}" if writable_profiles else "No profiles writable",
            recommendation="Ag3ntum sets HOME=/workspace with no profile sourcing"
        ))

        # Check 6.3: Git hooks writable
        git_hooks_dir = ".git/hooks/"
        git_hooks_writable = self._path_exists(git_hooks_dir) and self._path_writable(git_hooks_dir)
        self._add_check(CheckResult(
            check_id="PERS-003",
            name="Git hooks writability",
            category=Category.PERSISTENCE.value,
            severity=Severity.HIGH.value if git_hooks_writable else Severity.MITIGATED.value,
            status="VULNERABLE" if git_hooks_writable else "PROTECTED",
            description="Check if git hooks directory is writable (code execution on git operations)",
            details=f"Git hooks writable: {git_hooks_writable}",
            recommendation="Ag3ntum blocklists .git/** pattern"
        ))

        # Check 6.4: Python startup file
        python_startup = os.environ.get("PYTHONSTARTUP", "")
        pythonpath = os.environ.get("PYTHONPATH", "")
        self._add_check(CheckResult(
            check_id="PERS-004",
            name="Python environment manipulation",
            category=Category.PERSISTENCE.value,
            severity=Severity.MEDIUM.value if python_startup or pythonpath else Severity.MITIGATED.value,
            status="PARTIAL" if python_startup or pythonpath else "PROTECTED",
            description="Check if PYTHONSTARTUP or PYTHONPATH are set (code injection)",
            details=f"PYTHONSTARTUP: {python_startup or 'not set'}, PYTHONPATH: {pythonpath or 'not set'}",
            recommendation="Ag3ntum clears environment with --clearenv"
        ))

    # =========================================================================
    # Category 7: Audit and Compliance
    # =========================================================================

    def check_audit(self) -> None:
        """Check audit and compliance risks."""
        if self.verbose:
            print("\n[Category 7: Audit and Compliance]")

        # Check 7.1: Log directory access
        log_dirs = [
            "/var/log/",
            "~/.local/share/",
            "./logs/",
        ]
        writable_logs = [d for d in log_dirs if self._path_writable(d)]
        self._add_check(CheckResult(
            check_id="AUDIT-001",
            name="Log directory writability",
            category=Category.AUDIT.value,
            severity=Severity.MEDIUM.value if "/var/log/" in writable_logs else Severity.LOW.value if writable_logs else Severity.MITIGATED.value,
            status="VULNERABLE" if writable_logs else "PROTECTED",
            description="Check if log directories are writable (log tampering risk)",
            details=f"Writable: {writable_logs}" if writable_logs else "No log dirs writable",
            recommendation="Ag3ntum logs are protected by system ownership"
        ))

        # Check 7.2: History files
        history_files = [
            "~/.bash_history",
            "~/.zsh_history",
            "~/.python_history",
        ]
        readable_history = [f for f in history_files if self._path_readable(f)]
        self._add_check(CheckResult(
            check_id="AUDIT-002",
            name="Command history accessibility",
            category=Category.AUDIT.value,
            severity=Severity.MEDIUM.value if readable_history else Severity.MITIGATED.value,
            status="VULNERABLE" if readable_history else "PROTECTED",
            description="Check if command history files are accessible",
            details=f"Accessible: {readable_history}" if readable_history else "No history files accessible",
            recommendation="Ag3ntum does not mount home directory history files"
        ))

    # =========================================================================
    # Category 8: Container-Specific Checks
    # =========================================================================

    def check_container_security(self) -> None:
        """Check container-specific security concerns."""
        if self.verbose:
            print("\n[Category 8: Container Security]")

        # Only relevant if in Docker
        if not self.report.is_docker:
            self._add_check(CheckResult(
                check_id="CONT-000",
                name="Container detection",
                category="Container Security",
                severity=Severity.INFO.value,
                status="N/A",
                description="Check if running in a container",
                details="Not running in a container environment"
            ))
            return

        # Check 8.1: Running as root in container
        self._add_check(CheckResult(
            check_id="CONT-001",
            name="Container root user",
            category="Container Security",
            severity=Severity.HIGH.value if self.report.is_root else Severity.MITIGATED.value,
            status="VULNERABLE" if self.report.is_root else "PROTECTED",
            description="Check if container is running as root",
            details=f"UID: {self.report.uid}",
            recommendation="Use non-root user (UID 50000+) in containers"
        ))

        # Check 8.2: Sensitive host mounts
        sensitive_mounts = []
        mount_checks = [
            ("/etc", "Host /etc"),
            ("/root", "Host root home"),
            ("/home", "Host home directories"),
            ("/var/run/docker.sock", "Docker socket"),
        ]
        for path, name in mount_checks:
            if self._path_exists(path) and self._path_readable(path):
                # Check if it looks like it contains host data (not container-only)
                if path == "/etc" and self._path_exists("/etc/hostname"):
                    sensitive_mounts.append(name)
                elif path != "/etc":
                    sensitive_mounts.append(name)

        self._add_check(CheckResult(
            check_id="CONT-002",
            name="Sensitive host mounts",
            category="Container Security",
            severity=Severity.HIGH.value if sensitive_mounts else Severity.MITIGATED.value,
            status="VULNERABLE" if sensitive_mounts else "PROTECTED",
            description="Check for sensitive host directory mounts",
            details=f"Sensitive mounts detected: {sensitive_mounts}" if sensitive_mounts else "No sensitive host mounts",
            recommendation="Minimize host mounts, never mount /, /etc, /root, ~/.ssh"
        ))

    # =========================================================================
    # Run All Checks
    # =========================================================================

    def run_assessment(self) -> AssessmentReport:
        """Run complete security assessment."""
        if self.verbose:
            print("=" * 60)
            print("Claude Code Security Environment Assessment")
            print("=" * 60)
            print(f"\nHost: {self.report.hostname}")
            print(f"Platform: {self.report.platform}")
            print(f"User: {self.report.user} (UID: {self.report.uid})")
            print(f"In Docker: {self.report.is_docker}")
            print(f"Running as root: {self.report.is_root}")

        # Run all category checks
        self.check_filesystem_access()
        self.check_command_execution()
        self.check_process_disclosure()
        self.check_network_security()
        self.check_secrets_exposure()
        self.check_persistence()
        self.check_audit()
        self.check_container_security()

        # Compile results
        self.report.checks = [asdict(c) for c in self.checks]

        # Generate summary
        severity_counts = {s.value: 0 for s in Severity}
        status_counts = {"VULNERABLE": 0, "PROTECTED": 0, "PARTIAL": 0, "N/A": 0}

        for check in self.checks:
            severity_counts[check.severity] = severity_counts.get(check.severity, 0) + 1
            status_counts[check.status] = status_counts.get(check.status, 0) + 1

        self.report.summary = {
            "total_checks": len(self.checks),
            "by_severity": severity_counts,
            "by_status": status_counts,
            "risk_score": self._calculate_risk_score(),
            "risk_level": self._get_risk_level()
        }

        return self.report

    def _calculate_risk_score(self) -> int:
        """Calculate overall risk score (0-100, higher = more risk)."""
        weights = {
            Severity.CRITICAL.value: 25,
            Severity.HIGH.value: 15,
            Severity.MEDIUM.value: 5,
            Severity.LOW.value: 1,
            Severity.MITIGATED.value: 0,
            Severity.INFO.value: 0
        }

        score = 0
        for check in self.checks:
            if check.status == "VULNERABLE":
                score += weights.get(check.severity, 0)
            elif check.status == "PARTIAL":
                score += weights.get(check.severity, 0) // 2

        return min(100, score)

    def _get_risk_level(self) -> str:
        """Get overall risk level label."""
        score = self._calculate_risk_score()
        if score >= 75:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 25:
            return "MEDIUM"
        elif score > 0:
            return "LOW"
        else:
            return "MINIMAL"

    def print_report(self) -> None:
        """Print human-readable report to console."""
        print("\n" + "=" * 60)
        print("SECURITY ASSESSMENT SUMMARY")
        print("=" * 60)

        summary = self.report.summary
        print(f"\nTotal Checks: {summary['total_checks']}")
        print(f"Risk Score: {summary['risk_score']}/100")
        print(f"Risk Level: {summary['risk_level']}")

        print("\nBy Severity:")
        for severity, count in summary['by_severity'].items():
            if count > 0:
                print(f"  {severity}: {count}")

        print("\nBy Status:")
        for status, count in summary['by_status'].items():
            if count > 0:
                symbol = {"VULNERABLE": "\u274c", "PROTECTED": "\u2705", "PARTIAL": "\u26a0\ufe0f", "N/A": "\u2796"}[status]
                print(f"  {symbol} {status}: {count}")

        # Print critical and high findings
        critical_high = [c for c in self.checks
                        if c.severity in (Severity.CRITICAL.value, Severity.HIGH.value)
                        and c.status == "VULNERABLE"]

        if critical_high:
            print("\n" + "=" * 60)
            print("CRITICAL/HIGH FINDINGS REQUIRING ATTENTION")
            print("=" * 60)
            for check in critical_high:
                print(f"\n[{check.severity}] {check.check_id}: {check.name}")
                print(f"  Status: {check.status}")
                print(f"  Details: {check.details}")
                print(f"  Recommendation: {check.recommendation}")

        print("\n" + "=" * 60)
        print("RECOMMENDATIONS")
        print("=" * 60)

        if summary['risk_level'] in ("CRITICAL", "HIGH"):
            print("""
\u26a0\ufe0f  This environment has SIGNIFICANT security risks for running AI agents.

Immediate actions:
1. Do NOT run Claude Code as root
2. Use a container (Docker) with minimal mounts
3. Remove or restrict access to SSH keys, AWS credentials
4. Consider using Ag3ntum for enterprise-grade security

Ag3ntum provides:
- 7-layer defense-in-depth security model
- Per-user UID isolation (50000-60000 range)
- Bubblewrap sandboxing with filtered /proc
- PathValidator blocking cross-user/session access
- Command security filter (140+ patterns)
- Secrets scanning and redaction
""")
        elif summary['risk_level'] == "MEDIUM":
            print("""
\u26a0\ufe0f  This environment has MODERATE security risks.

Recommendations:
1. Review and restrict file access where possible
2. Consider network isolation
3. Monitor command execution
4. For production use, consider Ag3ntum
""")
        else:
            print("""
\u2705 This environment has LOW security risk.

For additional hardening, consider:
- Ag3ntum for multi-tenant deployments
- Network isolation if not needed
- Regular security audits
""")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Non-invasive security assessment for Claude Code environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python %(prog)s                    # Run assessment with console output
  python %(prog)s --verbose          # Detailed progress output
  python %(prog)s --json             # Output as JSON
  python %(prog)s --json > report.json  # Save JSON report
"""
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed progress during assessment"
    )

    args = parser.parse_args()

    # Run assessment
    assessment = SecurityAssessment(verbose=args.verbose and not args.json)
    report = assessment.run_assessment()

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        assessment.print_report()

    # Exit with code based on risk level
    risk_level = report.summary.get("risk_level", "UNKNOWN")
    if risk_level == "CRITICAL":
        sys.exit(3)
    elif risk_level == "HIGH":
        sys.exit(2)
    elif risk_level == "MEDIUM":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
