#!/usr/bin/env python3
"""
Red Alert - System Discovery Tool for Authorized Security Testing

This script performs comprehensive system enumeration for:
- Authorized penetration testing engagements
- Security audits and assessments
- CTF competitions
- Defensive security (understanding attacker perspective)

DISCLAIMER: Only use on systems you own or have explicit written authorization to test.
Unauthorized access to computer systems is illegal.

Usage: python3 red-alert.py [--output report.txt] [--json] [--quick]
"""

import argparse
import datetime
import glob
import grp
import hashlib
import json
import os
import platform
import pwd
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class Colors:
    """ANSI color codes for terminal output."""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class RedAlert:
    """System discovery and enumeration tool for security assessments."""

    def __init__(self, json_output: bool = False, quick_mode: bool = False):
        self.json_output = json_output
        self.quick_mode = quick_mode
        self.findings: Dict[str, Any] = {}
        self.sensitive_files_found: List[Dict[str, Any]] = []
        self.is_root = os.geteuid() == 0 if hasattr(os, 'geteuid') else False

    def run_command(self, cmd: str, timeout: int = 30) -> Tuple[str, str, int]:
        """Execute a shell command and return stdout, stderr, return code."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timed out", -1
        except Exception as e:
            return "", str(e), -1

    def print_section(self, title: str):
        """Print a formatted section header."""
        if not self.json_output:
            print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.YELLOW}[*] {title}{Colors.RESET}")
            print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")

    def print_finding(self, label: str, value: str, severity: str = "info"):
        """Print a finding with color-coded severity."""
        if self.json_output:
            return

        colors = {
            "critical": Colors.RED,
            "high": Colors.MAGENTA,
            "medium": Colors.YELLOW,
            "low": Colors.BLUE,
            "info": Colors.WHITE
        }
        color = colors.get(severity, Colors.WHITE)
        print(f"  {Colors.GREEN}[+]{Colors.RESET} {label}: {color}{value}{Colors.RESET}")

    def print_warning(self, message: str):
        """Print a warning message."""
        if not self.json_output:
            print(f"  {Colors.RED}[!] WARNING: {message}{Colors.RESET}")

    def get_system_info(self) -> Dict[str, Any]:
        """Gather basic system information."""
        self.print_section("SYSTEM INFORMATION")

        info = {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        }

        # Get detailed kernel info
        uname_out, _, _ = self.run_command("uname -a")
        info["kernel_full"] = uname_out

        # Get OS release info
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        info[f"os_{key.lower()}"] = value.strip('"')

        # macOS specific
        if platform.system() == "Darwin":
            sw_vers, _, _ = self.run_command("sw_vers")
            info["macos_version"] = sw_vers

        # Get boot time
        if platform.system() == "Linux":
            uptime_out, _, _ = self.run_command("uptime -s 2>/dev/null || uptime")
            info["uptime"] = uptime_out
        elif platform.system() == "Darwin":
            uptime_out, _, _ = self.run_command("uptime")
            info["uptime"] = uptime_out

        for key, value in info.items():
            if value:
                self.print_finding(key.replace("_", " ").title(), str(value)[:100])

        self.findings["system_info"] = info
        return info

    def get_kernel_info(self) -> Dict[str, Any]:
        """Gather detailed kernel and security module information."""
        self.print_section("KERNEL & SECURITY MODULES")

        info = {}

        # Kernel version
        kernel_ver, _, _ = self.run_command("uname -r")
        info["kernel_version"] = kernel_ver
        self.print_finding("Kernel Version", kernel_ver)

        # Check for kernel vulnerabilities (basic checks)
        if platform.system() == "Linux":
            # Check kernel config if available
            kernel_config_paths = [
                f"/boot/config-{kernel_ver}",
                "/proc/config.gz"
            ]
            for config_path in kernel_config_paths:
                if os.path.exists(config_path):
                    info["kernel_config_path"] = config_path
                    self.print_finding("Kernel Config", config_path)
                    break

            # Check loaded kernel modules
            lsmod_out, _, _ = self.run_command("lsmod 2>/dev/null")
            if lsmod_out:
                modules = lsmod_out.split('\n')[1:]  # Skip header
                info["loaded_modules"] = [m.split()[0] for m in modules if m]
                self.print_finding("Loaded Modules Count", str(len(info["loaded_modules"])))

            # Check security modules
            security_modules = []

            # SELinux
            selinux_out, _, ret = self.run_command("getenforce 2>/dev/null")
            if ret == 0:
                security_modules.append(f"SELinux: {selinux_out}")

            # AppArmor
            apparmor_out, _, ret = self.run_command("aa-status 2>/dev/null | head -1")
            if ret == 0:
                security_modules.append(f"AppArmor: Active")

            # Check ASLR
            if os.path.exists("/proc/sys/kernel/randomize_va_space"):
                with open("/proc/sys/kernel/randomize_va_space", "r") as f:
                    aslr = f.read().strip()
                    aslr_status = {
                        "0": "Disabled (VULNERABLE)",
                        "1": "Conservative",
                        "2": "Full"
                    }.get(aslr, "Unknown")
                    info["aslr"] = aslr_status
                    severity = "critical" if aslr == "0" else "info"
                    self.print_finding("ASLR Status", aslr_status, severity)

            info["security_modules"] = security_modules
            for mod in security_modules:
                self.print_finding("Security Module", mod)

        elif platform.system() == "Darwin":
            # macOS SIP status
            sip_out, _, _ = self.run_command("csrutil status 2>/dev/null")
            info["sip_status"] = sip_out
            if sip_out:
                self.print_finding("SIP Status", sip_out)

            # Gatekeeper status
            gk_out, _, _ = self.run_command("spctl --status 2>/dev/null")
            info["gatekeeper"] = gk_out
            if gk_out:
                self.print_finding("Gatekeeper", gk_out)

        self.findings["kernel_info"] = info
        return info

    def get_user_info(self) -> Dict[str, Any]:
        """Gather user and privilege information."""
        self.print_section("USER & PRIVILEGE INFORMATION")

        info = {
            "current_user": os.getenv("USER") or pwd.getpwuid(os.getuid()).pw_name,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "euid": os.geteuid() if hasattr(os, 'geteuid') else None,
            "egid": os.getegid() if hasattr(os, 'getegid') else None,
            "home": os.path.expanduser("~"),
            "shell": os.getenv("SHELL"),
        }

        # Check if running as root
        if info["euid"] == 0:
            self.print_warning("Running as ROOT!")
            info["is_root"] = True
        else:
            info["is_root"] = False

        for key, value in info.items():
            self.print_finding(key.replace("_", " ").title(), str(value))

        # Get groups
        groups_out, _, _ = self.run_command("groups")
        info["groups"] = groups_out.split() if groups_out else []
        self.print_finding("Groups", groups_out)

        # Check sudo privileges
        sudo_out, _, ret = self.run_command("sudo -l 2>/dev/null")
        if ret == 0 and sudo_out:
            info["sudo_privileges"] = sudo_out
            self.print_finding("Sudo Privileges", "Available (check details)", "high")

        # List all users
        users = []
        try:
            for p in pwd.getpwall():
                user_info = {
                    "username": p.pw_name,
                    "uid": p.pw_uid,
                    "gid": p.pw_gid,
                    "home": p.pw_dir,
                    "shell": p.pw_shell
                }
                # Check if user has a login shell
                if p.pw_shell not in ["/bin/false", "/usr/sbin/nologin", "/sbin/nologin"]:
                    user_info["can_login"] = True
                users.append(user_info)
        except Exception:
            pass

        info["all_users"] = users
        login_users = [u for u in users if u.get("can_login")]
        self.print_finding("Users with Login Shell", str(len(login_users)))

        # Check for logged in users
        who_out, _, _ = self.run_command("who 2>/dev/null")
        if who_out:
            info["logged_in_users"] = who_out.split('\n')
            self.print_finding("Currently Logged In", str(len(info["logged_in_users"])))

        # Last logins
        if not self.quick_mode:
            last_out, _, _ = self.run_command("last -n 10 2>/dev/null")
            info["last_logins"] = last_out

        self.findings["user_info"] = info
        return info

    def get_network_info(self) -> Dict[str, Any]:
        """Gather network configuration and connections."""
        self.print_section("NETWORK INFORMATION")

        info = {}

        # Hostname and domain
        info["hostname"] = socket.gethostname()
        try:
            info["fqdn"] = socket.getfqdn()
        except Exception:
            pass

        # Get IP addresses
        if platform.system() == "Darwin":
            ip_out, _, _ = self.run_command("ifconfig | grep 'inet ' | grep -v 127.0.0.1")
        else:
            ip_out, _, _ = self.run_command("ip addr show 2>/dev/null || ifconfig 2>/dev/null")
        info["ip_addresses"] = ip_out
        self.print_finding("IP Configuration", ip_out[:200] if ip_out else "N/A")

        # Get routing table
        if platform.system() == "Darwin":
            route_out, _, _ = self.run_command("netstat -rn 2>/dev/null | head -20")
        else:
            route_out, _, _ = self.run_command("ip route 2>/dev/null || route -n 2>/dev/null")
        info["routing"] = route_out

        # Get DNS servers
        if os.path.exists("/etc/resolv.conf"):
            with open("/etc/resolv.conf", "r") as f:
                dns_servers = [line.split()[1] for line in f if line.startswith("nameserver")]
                info["dns_servers"] = dns_servers
                self.print_finding("DNS Servers", ", ".join(dns_servers))

        # Get listening ports
        if platform.system() == "Darwin":
            listen_out, _, _ = self.run_command("netstat -an | grep LISTEN")
        else:
            listen_out, _, _ = self.run_command("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
        info["listening_ports"] = listen_out
        if listen_out:
            port_count = len([l for l in listen_out.split('\n') if l.strip()])
            self.print_finding("Listening Ports", str(port_count), "medium")

        # Get established connections
        if platform.system() == "Darwin":
            conn_out, _, _ = self.run_command("netstat -an | grep ESTABLISHED | head -20")
        else:
            conn_out, _, _ = self.run_command("ss -tnp 2>/dev/null | head -20 || netstat -tnp 2>/dev/null | head -20")
        info["established_connections"] = conn_out

        # Check for common network services
        services = {
            "ssh": 22,
            "http": 80,
            "https": 443,
            "mysql": 3306,
            "postgres": 5432,
            "mongodb": 27017,
            "redis": 6379,
            "ftp": 21,
            "smtp": 25,
            "dns": 53
        }

        open_services = []
        for service, port in services.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                if result == 0:
                    open_services.append(f"{service}:{port}")
                sock.close()
            except Exception:
                pass

        info["common_services"] = open_services
        if open_services:
            self.print_finding("Open Common Services", ", ".join(open_services), "medium")

        # ARP cache
        arp_out, _, _ = self.run_command("arp -a 2>/dev/null | head -20")
        info["arp_cache"] = arp_out

        # Firewall status
        if platform.system() == "Darwin":
            fw_out, _, _ = self.run_command("/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null")
            info["firewall"] = fw_out
            if fw_out:
                self.print_finding("Firewall Status", fw_out)
        else:
            # Check iptables
            ipt_out, _, ret = self.run_command("iptables -L -n 2>/dev/null | head -10")
            if ret == 0:
                info["iptables"] = ipt_out
                self.print_finding("IPTables", "Rules present" if ipt_out else "No rules")

        self.findings["network_info"] = info
        return info

    def get_process_info(self) -> Dict[str, Any]:
        """Gather running process information."""
        self.print_section("PROCESS INFORMATION")

        info = {}

        # Get all processes
        if platform.system() == "Darwin":
            ps_out, _, _ = self.run_command("ps aux")
        else:
            ps_out, _, _ = self.run_command("ps auxf 2>/dev/null || ps aux")

        processes = []
        lines = ps_out.split('\n')
        header = lines[0] if lines else ""

        for line in lines[1:]:
            if line.strip():
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    proc = {
                        "user": parts[0],
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "command": parts[10]
                    }
                    processes.append(proc)

        info["process_count"] = len(processes)
        info["processes"] = processes
        self.print_finding("Total Processes", str(len(processes)))

        # Find interesting processes
        interesting_procs = []
        interesting_keywords = [
            "ssh", "sshd", "apache", "nginx", "mysql", "postgres", "mongo",
            "redis", "docker", "containerd", "kubelet", "vault", "consul",
            "aws", "gcp", "azure", "terraform", "ansible", "puppet", "chef",
            "cron", "systemd", "supervisord", "python", "node", "java", "go"
        ]

        for proc in processes:
            cmd_lower = proc["command"].lower()
            for keyword in interesting_keywords:
                if keyword in cmd_lower:
                    interesting_procs.append(proc)
                    break

        info["interesting_processes"] = interesting_procs
        self.print_finding("Interesting Processes", str(len(interesting_procs)), "medium")

        # Find processes running as root
        root_procs = [p for p in processes if p["user"] == "root"]
        info["root_processes"] = len(root_procs)
        self.print_finding("Root Processes", str(len(root_procs)))

        # Check for processes with high CPU/memory
        high_resource = [p for p in processes if float(p.get("cpu", 0)) > 10 or float(p.get("mem", 0)) > 10]
        if high_resource:
            info["high_resource_processes"] = high_resource
            self.print_finding("High Resource Processes", str(len(high_resource)), "low")

        self.findings["process_info"] = info
        return info

    def get_filesystem_info(self) -> Dict[str, Any]:
        """Gather filesystem and mount information."""
        self.print_section("FILESYSTEM INFORMATION")

        info = {}

        # Get mount points
        mount_out, _, _ = self.run_command("mount")
        info["mounts"] = mount_out

        # Get disk usage
        df_out, _, _ = self.run_command("df -h")
        info["disk_usage"] = df_out
        self.print_finding("Disk Usage", "See detailed output")

        # Important system directories
        system_dirs = [
            "/etc", "/var", "/tmp", "/opt", "/home", "/root",
            "/usr/local", "/var/log", "/var/www", "/srv"
        ]

        accessible_dirs = []
        for dir_path in system_dirs:
            if os.path.exists(dir_path):
                readable = os.access(dir_path, os.R_OK)
                writable = os.access(dir_path, os.W_OK)
                status = []
                if readable:
                    status.append("R")
                if writable:
                    status.append("W")
                accessible_dirs.append({
                    "path": dir_path,
                    "readable": readable,
                    "writable": writable,
                    "status": "".join(status) if status else "None"
                })

                severity = "high" if writable and dir_path in ["/etc", "/root", "/var"] else "info"
                self.print_finding(f"Dir: {dir_path}", f"[{''.join(status) if status else 'No access'}]", severity)

        info["system_directories"] = accessible_dirs

        # Find world-writable directories
        if not self.quick_mode:
            ww_dirs = []
            for root_dir in ["/tmp", "/var/tmp", "/dev/shm"]:
                if os.path.exists(root_dir):
                    for root, dirs, files in os.walk(root_dir):
                        try:
                            if os.stat(root).st_mode & stat.S_IWOTH:
                                ww_dirs.append(root)
                        except (OSError, PermissionError):
                            pass
                        if len(ww_dirs) > 50:
                            break

            info["world_writable_dirs"] = ww_dirs[:20]
            if ww_dirs:
                self.print_finding("World-Writable Dirs", str(len(ww_dirs)), "medium")

        # Check for SUID/SGID binaries
        if not self.quick_mode:
            suid_out, _, _ = self.run_command(
                "find /usr /bin /sbin -perm -4000 -type f 2>/dev/null | head -30"
            )
            if suid_out:
                suid_files = suid_out.split('\n')
                info["suid_binaries"] = suid_files
                self.print_finding("SUID Binaries", str(len(suid_files)), "high")

                # Check for unusual SUID binaries
                common_suid = [
                    "passwd", "sudo", "su", "ping", "mount", "umount",
                    "chsh", "newgrp", "gpasswd", "chfn"
                ]
                unusual_suid = [f for f in suid_files if not any(c in f for c in common_suid)]
                if unusual_suid:
                    info["unusual_suid"] = unusual_suid
                    self.print_finding("Unusual SUID", str(len(unusual_suid)), "critical")

        self.findings["filesystem_info"] = info
        return info

    def find_sensitive_files(self) -> Dict[str, Any]:
        """Search for potentially sensitive files."""
        self.print_section("SENSITIVE FILE DISCOVERY")

        info = {"files": []}

        # SSH keys and configs
        ssh_patterns = [
            os.path.expanduser("~/.ssh/*"),
            "/etc/ssh/*",
            "/root/.ssh/*",
            "/home/*/.ssh/*"
        ]

        self.print_finding("Scanning", "SSH keys and configs...")
        for pattern in ssh_patterns:
            for filepath in glob.glob(pattern):
                try:
                    st = os.stat(filepath)
                    readable = os.access(filepath, os.R_OK)

                    file_info = {
                        "path": filepath,
                        "type": "ssh",
                        "readable": readable,
                        "permissions": oct(st.st_mode)[-3:],
                        "owner": pwd.getpwuid(st.st_uid).pw_name if st.st_uid else "unknown"
                    }

                    # Check if it's a private key
                    if readable and os.path.isfile(filepath):
                        try:
                            with open(filepath, 'r') as f:
                                content = f.read(100)
                                if "PRIVATE KEY" in content:
                                    file_info["is_private_key"] = True
                                    self.print_warning(f"Private key found: {filepath}")
                        except Exception:
                            pass

                    info["files"].append(file_info)
                    severity = "critical" if file_info.get("is_private_key") else "medium"
                    self.print_finding(f"SSH File", filepath, severity)

                except (OSError, PermissionError, KeyError):
                    pass

        # Config files with potential credentials
        sensitive_configs = [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/group",
            "/etc/sudoers",
            "/etc/hosts",
            "/etc/crontab",
            "~/.bashrc",
            "~/.bash_history",
            "~/.zsh_history",
            "~/.mysql_history",
            "~/.psql_history",
            "~/.gitconfig",
            "~/.netrc",
            "~/.aws/credentials",
            "~/.aws/config",
            "~/.docker/config.json",
            "~/.kube/config",
            "/etc/mysql/my.cnf",
            "/etc/postgresql/*/main/pg_hba.conf",
            "/var/www/html/wp-config.php",
            "/var/www/html/.env",
            "~/.env",
            ".env"
        ]

        self.print_finding("Scanning", "Configuration files...")
        for config in sensitive_configs:
            filepath = os.path.expanduser(config)

            # Handle glob patterns
            if '*' in filepath:
                matches = glob.glob(filepath)
            else:
                matches = [filepath] if os.path.exists(filepath) else []

            for match in matches:
                try:
                    st = os.stat(match)
                    readable = os.access(match, os.R_OK)

                    file_info = {
                        "path": match,
                        "type": "config",
                        "readable": readable,
                        "permissions": oct(st.st_mode)[-3:],
                        "size": st.st_size
                    }

                    # Check permissions on sensitive files
                    if match == "/etc/shadow" and readable:
                        self.print_warning("Shadow file is readable!")
                        file_info["warning"] = "Shadow file readable by current user"

                    info["files"].append(file_info)
                    severity = "high" if readable and "shadow" in match else "info"
                    self.print_finding(f"Config", f"{match} [{file_info['permissions']}]", severity)

                except (OSError, PermissionError):
                    pass

        # Search for files with sensitive patterns in common locations
        if not self.quick_mode:
            self.print_finding("Scanning", "Files with sensitive patterns...")
            sensitive_patterns = [
                "*.pem", "*.key", "*.p12", "*.pfx",
                "*password*", "*secret*", "*credential*",
                "*.db", "*.sqlite", "*.sqlite3",
                ".env*", "*.conf", "config.*"
            ]

            search_dirs = [
                os.path.expanduser("~"),
                "/tmp",
                "/var/tmp",
                "/opt"
            ]

            for search_dir in search_dirs:
                if os.path.exists(search_dir) and os.access(search_dir, os.R_OK):
                    for pattern in sensitive_patterns[:5]:  # Limit patterns
                        try:
                            for filepath in glob.glob(os.path.join(search_dir, "**", pattern), recursive=True):
                                try:
                                    st = os.stat(filepath)
                                    if st.st_size < 10 * 1024 * 1024:  # < 10MB
                                        file_info = {
                                            "path": filepath,
                                            "type": "pattern_match",
                                            "readable": os.access(filepath, os.R_OK),
                                            "size": st.st_size
                                        }
                                        info["files"].append(file_info)
                                except (OSError, PermissionError):
                                    pass
                        except Exception:
                            pass

        self.findings["sensitive_files"] = info
        return info

    def get_environment_info(self) -> Dict[str, Any]:
        """Gather environment variables and paths."""
        self.print_section("ENVIRONMENT INFORMATION")

        info = {}

        # Get all environment variables
        env_vars = dict(os.environ)

        # Sensitive env var patterns
        sensitive_patterns = [
            "key", "secret", "password", "token", "auth",
            "credential", "api", "private", "passwd"
        ]

        sensitive_vars = {}
        safe_vars = {}

        for key, value in env_vars.items():
            key_lower = key.lower()
            is_sensitive = any(p in key_lower for p in sensitive_patterns)

            if is_sensitive:
                # Mask the value
                masked_value = value[:4] + "***" + value[-4:] if len(value) > 10 else "***"
                sensitive_vars[key] = masked_value
                self.print_finding(f"Sensitive Env", f"{key}={masked_value}", "high")
            else:
                safe_vars[key] = value

        info["sensitive_env_vars"] = list(sensitive_vars.keys())
        info["path"] = os.getenv("PATH", "").split(":")
        info["shell"] = os.getenv("SHELL")
        info["term"] = os.getenv("TERM")
        info["editor"] = os.getenv("EDITOR") or os.getenv("VISUAL")
        info["home"] = os.getenv("HOME")

        self.print_finding("PATH Entries", str(len(info["path"])))
        self.print_finding("Shell", info["shell"] or "Unknown")

        # Check for writable PATH directories
        writable_paths = []
        for path_dir in info["path"]:
            if os.path.exists(path_dir) and os.access(path_dir, os.W_OK):
                writable_paths.append(path_dir)

        if writable_paths:
            info["writable_path_dirs"] = writable_paths
            self.print_finding("Writable PATH Dirs", str(len(writable_paths)), "high")
            for wp in writable_paths:
                self.print_warning(f"Writable PATH: {wp}")

        self.findings["environment_info"] = info
        return info

    def get_scheduled_tasks(self) -> Dict[str, Any]:
        """Gather cron jobs and scheduled tasks."""
        self.print_section("SCHEDULED TASKS")

        info = {}

        # User crontab
        crontab_out, _, ret = self.run_command("crontab -l 2>/dev/null")
        if ret == 0 and crontab_out:
            info["user_crontab"] = crontab_out
            self.print_finding("User Crontab", "Jobs found", "medium")

        # System crontabs
        cron_dirs = [
            "/etc/crontab",
            "/etc/cron.d",
            "/etc/cron.daily",
            "/etc/cron.hourly",
            "/etc/cron.weekly",
            "/etc/cron.monthly",
            "/var/spool/cron",
            "/var/spool/cron/crontabs"
        ]

        for cron_path in cron_dirs:
            if os.path.exists(cron_path):
                readable = os.access(cron_path, os.R_OK)
                info[f"cron_{os.path.basename(cron_path)}"] = {
                    "path": cron_path,
                    "readable": readable
                }
                self.print_finding(f"Cron Path", f"{cron_path} [{'R' if readable else 'No access'}]")

        # Systemd timers (Linux)
        if platform.system() == "Linux":
            timers_out, _, ret = self.run_command("systemctl list-timers --all 2>/dev/null")
            if ret == 0 and timers_out:
                info["systemd_timers"] = timers_out
                self.print_finding("Systemd Timers", "Found")

        # LaunchAgents/Daemons (macOS)
        if platform.system() == "Darwin":
            launch_dirs = [
                "~/Library/LaunchAgents",
                "/Library/LaunchAgents",
                "/Library/LaunchDaemons",
                "/System/Library/LaunchAgents",
                "/System/Library/LaunchDaemons"
            ]

            for launch_dir in launch_dirs:
                expanded = os.path.expanduser(launch_dir)
                if os.path.exists(expanded) and os.access(expanded, os.R_OK):
                    try:
                        items = os.listdir(expanded)
                        info[f"launch_{os.path.basename(expanded)}"] = items[:20]
                        self.print_finding(f"Launch Items", f"{launch_dir}: {len(items)} items")
                    except (OSError, PermissionError):
                        pass

        self.findings["scheduled_tasks"] = info
        return info

    def get_installed_software(self) -> Dict[str, Any]:
        """Gather information about installed software."""
        self.print_section("INSTALLED SOFTWARE")

        info = {}

        # Package managers
        package_managers = {
            "dpkg": "dpkg -l 2>/dev/null | wc -l",
            "rpm": "rpm -qa 2>/dev/null | wc -l",
            "pacman": "pacman -Q 2>/dev/null | wc -l",
            "brew": "brew list 2>/dev/null | wc -l",
            "pip": "pip list 2>/dev/null | wc -l",
            "pip3": "pip3 list 2>/dev/null | wc -l",
            "npm": "npm list -g --depth=0 2>/dev/null | wc -l",
            "gem": "gem list 2>/dev/null | wc -l"
        }

        for pm, cmd in package_managers.items():
            count, _, ret = self.run_command(cmd)
            if ret == 0 and count.strip() and count.strip() != "0":
                info[f"{pm}_packages"] = int(count.strip())
                self.print_finding(f"{pm.upper()} Packages", count.strip())

        # Check for specific tools that might be useful
        security_tools = [
            "nmap", "nc", "netcat", "curl", "wget", "ssh", "scp",
            "python", "python3", "perl", "ruby", "php", "gcc", "make",
            "docker", "kubectl", "aws", "gcloud", "az",
            "git", "svn", "vim", "nano", "tmux", "screen"
        ]

        available_tools = []
        for tool in security_tools:
            which_out, _, ret = self.run_command(f"which {tool} 2>/dev/null")
            if ret == 0 and which_out:
                available_tools.append(tool)

        info["available_tools"] = available_tools
        self.print_finding("Available Tools", ", ".join(available_tools[:15]))

        # Docker info if available
        docker_out, _, ret = self.run_command("docker info 2>/dev/null | head -5")
        if ret == 0:
            info["docker"] = "Installed and accessible"
            self.print_finding("Docker", "Accessible", "high")

            # List containers
            containers_out, _, _ = self.run_command("docker ps -a 2>/dev/null")
            if containers_out:
                info["docker_containers"] = containers_out

        self.findings["installed_software"] = info
        return info

    def check_container_environment(self) -> Dict[str, Any]:
        """Check if running inside a container."""
        self.print_section("CONTAINER DETECTION")

        info = {"is_container": False, "type": None}

        # Check for Docker
        if os.path.exists("/.dockerenv"):
            info["is_container"] = True
            info["type"] = "docker"
            self.print_finding("Container", "Running inside Docker", "high")

        # Check cgroup for container hints
        if os.path.exists("/proc/1/cgroup"):
            try:
                with open("/proc/1/cgroup", "r") as f:
                    cgroup_content = f.read()
                    if "docker" in cgroup_content:
                        info["is_container"] = True
                        info["type"] = "docker"
                    elif "kubepods" in cgroup_content:
                        info["is_container"] = True
                        info["type"] = "kubernetes"
                    elif "lxc" in cgroup_content:
                        info["is_container"] = True
                        info["type"] = "lxc"
            except Exception:
                pass

        # Check for Kubernetes
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            info["is_container"] = True
            info["type"] = "kubernetes"
            info["k8s_service_host"] = os.getenv("KUBERNETES_SERVICE_HOST")
            self.print_finding("Kubernetes", "Running in K8s pod", "high")

        # Check for container escape possibilities
        if info["is_container"]:
            # Check for privileged mode
            if os.path.exists("/dev/sda") or os.path.exists("/dev/nvme0"):
                info["privileged_mode"] = True
                self.print_warning("Container may be running in privileged mode!")

            # Check for mounted docker socket
            if os.path.exists("/var/run/docker.sock"):
                info["docker_socket_mounted"] = True
                self.print_warning("Docker socket is mounted - potential escape vector!")

        if not info["is_container"]:
            self.print_finding("Container", "Not detected (running on host)")

        self.findings["container_info"] = info
        return info

    def check_sandbox_environment(self) -> Dict[str, Any]:
        """Check for sandbox environments and their configurations."""
        self.print_section("SANDBOX & ISOLATION DETECTION")

        info = {
            "is_sandboxed": False,
            "sandbox_types": [],
            "weaknesses": [],
            "namespace_isolation": {},
            "capabilities": {},
            "seccomp": {},
        }

        # =====================================================================
        # NAMESPACE ISOLATION CHECKS (Linux)
        # =====================================================================
        if platform.system() == "Linux":
            self.print_finding("Checking", "Linux namespace isolation...")

            # Read current process namespaces
            ns_types = ["user", "mnt", "pid", "net", "ipc", "uts", "cgroup"]
            current_ns = {}
            init_ns = {}

            for ns in ns_types:
                try:
                    current_link = os.readlink(f"/proc/self/ns/{ns}")
                    current_ns[ns] = current_link
                except (OSError, FileNotFoundError):
                    current_ns[ns] = None

                try:
                    init_link = os.readlink(f"/proc/1/ns/{ns}")
                    init_ns[ns] = init_link
                except (OSError, FileNotFoundError):
                    init_ns[ns] = None

            # Compare namespaces - if different from init, we're isolated
            isolated_ns = []
            shared_ns = []
            for ns in ns_types:
                if current_ns.get(ns) and init_ns.get(ns):
                    if current_ns[ns] != init_ns[ns]:
                        isolated_ns.append(ns)
                        info["is_sandboxed"] = True
                    else:
                        shared_ns.append(ns)

            info["namespace_isolation"]["isolated"] = isolated_ns
            info["namespace_isolation"]["shared"] = shared_ns

            if isolated_ns:
                self.print_finding("Isolated Namespaces", ", ".join(isolated_ns), "medium")
                info["sandbox_types"].append("namespace")
            if shared_ns:
                self.print_finding("Shared Namespaces", ", ".join(shared_ns), "info")

            # Check for user namespace - important for unprivileged sandboxing
            if "user" in shared_ns:
                info["weaknesses"].append("User namespace shared with host - limited isolation")
                self.print_warning("User namespace shared with host")

            # Check if we're in a new user namespace with mappings
            if os.path.exists("/proc/self/uid_map"):
                try:
                    with open("/proc/self/uid_map", "r") as f:
                        uid_map = f.read().strip()
                        if uid_map:
                            info["namespace_isolation"]["uid_map"] = uid_map
                            # Check for identity mapping (weak)
                            if "0          0" in uid_map or "         0          0" in uid_map:
                                info["weaknesses"].append("UID identity mapping - root inside = root outside")
                                self.print_warning("UID identity mapping detected (root=root)")
                except Exception:
                    pass

        # =====================================================================
        # BUBBLEWRAP (bwrap) DETECTION
        # =====================================================================
        self.print_finding("Checking", "Bubblewrap sandbox...")

        bwrap_indicators = {
            "bwrap_binary": False,
            "in_bwrap": False,
            "bwrap_version": None,
            "weaknesses": []
        }

        # Check if bwrap is installed
        bwrap_path, _, ret = self.run_command("which bwrap 2>/dev/null")
        if ret == 0 and bwrap_path:
            bwrap_indicators["bwrap_binary"] = bwrap_path
            self.print_finding("Bubblewrap Binary", bwrap_path)

            # Get version
            bwrap_ver, _, _ = self.run_command("bwrap --version 2>/dev/null")
            bwrap_indicators["bwrap_version"] = bwrap_ver

        # Check if we're inside a bwrap sandbox
        # bwrap typically creates a new mount namespace and user namespace
        if platform.system() == "Linux":
            # Check process tree for bwrap
            ppid_chain = []
            try:
                current_pid = os.getpid()
                for _ in range(10):  # Max 10 levels up
                    stat_path = f"/proc/{current_pid}/stat"
                    if os.path.exists(stat_path):
                        with open(stat_path, "r") as f:
                            stat_content = f.read()
                            parts = stat_content.split()
                            if len(parts) > 3:
                                ppid = int(parts[3])
                                comm = parts[1].strip("()")
                                ppid_chain.append((current_pid, comm))
                                if "bwrap" in comm.lower():
                                    bwrap_indicators["in_bwrap"] = True
                                    info["is_sandboxed"] = True
                                    info["sandbox_types"].append("bubblewrap")
                                    self.print_finding("Bubblewrap", "Running inside bwrap sandbox", "high")
                                if ppid <= 1:
                                    break
                                current_pid = ppid
                    else:
                        break
            except Exception:
                pass

            # Check for bwrap-specific environment
            if os.getenv("container") == "bwrap":
                bwrap_indicators["in_bwrap"] = True
                info["is_sandboxed"] = True

            # Check for Flatpak (uses bwrap internally)
            if os.path.exists("/.flatpak-info"):
                bwrap_indicators["in_bwrap"] = True
                bwrap_indicators["flatpak"] = True
                info["is_sandboxed"] = True
                info["sandbox_types"].append("flatpak")
                self.print_finding("Flatpak", "Running inside Flatpak sandbox", "high")

                # Read Flatpak info for weakness analysis
                try:
                    with open("/.flatpak-info", "r") as f:
                        flatpak_info = f.read()
                        info["flatpak_info"] = flatpak_info

                        # Check for filesystem access
                        if "filesystems=host" in flatpak_info:
                            info["weaknesses"].append("Flatpak has full host filesystem access")
                            self.print_warning("Flatpak has host filesystem access!")
                        if "filesystems=home" in flatpak_info:
                            info["weaknesses"].append("Flatpak has home directory access")
                            self.print_warning("Flatpak has home directory access")
                except Exception:
                    pass

        info["bubblewrap"] = bwrap_indicators

        # =====================================================================
        # FIREJAIL DETECTION
        # =====================================================================
        self.print_finding("Checking", "Firejail sandbox...")

        firejail_info = {
            "binary": False,
            "in_firejail": False,
            "version": None,
            "profile": None,
            "weaknesses": []
        }

        # Check if firejail is installed
        firejail_path, _, ret = self.run_command("which firejail 2>/dev/null")
        if ret == 0 and firejail_path:
            firejail_info["binary"] = firejail_path
            self.print_finding("Firejail Binary", firejail_path)

            # Get version
            fj_ver, _, _ = self.run_command("firejail --version 2>/dev/null | head -1")
            firejail_info["version"] = fj_ver

            # Check for SUID on firejail (potential weakness)
            try:
                fj_stat = os.stat(firejail_path)
                if fj_stat.st_mode & stat.S_ISUID:
                    firejail_info["is_suid"] = True
                    self.print_finding("Firejail SUID", "Binary is SUID root", "medium")
            except Exception:
                pass

        # Check if we're inside firejail
        if platform.system() == "Linux":
            # Firejail sets specific environment variables
            if os.getenv("FIREJAIL"):
                firejail_info["in_firejail"] = True
                info["is_sandboxed"] = True
                info["sandbox_types"].append("firejail")
                self.print_finding("Firejail", "Running inside Firejail sandbox", "high")

            # Check for firejail in process tree
            ps_out, _, _ = self.run_command("ps -ef 2>/dev/null | grep -E 'firejail|firemon' | grep -v grep")
            if ps_out:
                firejail_info["processes"] = ps_out.split('\n')

            # Check firejail configuration weaknesses
            firejail_config_paths = [
                "/etc/firejail",
                os.path.expanduser("~/.config/firejail")
            ]

            for config_dir in firejail_config_paths:
                if os.path.exists(config_dir) and os.access(config_dir, os.R_OK):
                    try:
                        profiles = os.listdir(config_dir)
                        firejail_info["profiles_dir"] = config_dir
                        firejail_info["profile_count"] = len(profiles)

                        # Check for overly permissive profiles
                        for profile in profiles[:10]:
                            profile_path = os.path.join(config_dir, profile)
                            if profile_path.endswith(".profile") and os.path.isfile(profile_path):
                                try:
                                    with open(profile_path, "r") as f:
                                        content = f.read()
                                        if "noblacklist" in content:
                                            firejail_info["weaknesses"].append(f"{profile}: uses noblacklist")
                                        if "allow-debuggers" in content:
                                            firejail_info["weaknesses"].append(f"{profile}: allows debuggers")
                                            info["weaknesses"].append(f"Firejail profile allows debuggers: {profile}")
                                        if "caps.keep all" in content:
                                            firejail_info["weaknesses"].append(f"{profile}: keeps all capabilities")
                                            info["weaknesses"].append(f"Firejail keeps all caps: {profile}")
                                except Exception:
                                    pass
                    except Exception:
                        pass

        info["firejail"] = firejail_info

        # =====================================================================
        # SECCOMP DETECTION
        # =====================================================================
        self.print_finding("Checking", "Seccomp filters...")

        seccomp_info = {
            "status": "unknown",
            "mode": None,
            "filter_count": 0,
            "weaknesses": []
        }

        if platform.system() == "Linux":
            # Check seccomp status from /proc/self/status
            if os.path.exists("/proc/self/status"):
                try:
                    with open("/proc/self/status", "r") as f:
                        for line in f:
                            if line.startswith("Seccomp:"):
                                mode = line.split(":")[1].strip()
                                seccomp_info["mode"] = int(mode)
                                modes = {
                                    0: "Disabled",
                                    1: "Strict",
                                    2: "Filter"
                                }
                                seccomp_info["status"] = modes.get(int(mode), "Unknown")

                                if int(mode) == 0:
                                    seccomp_info["weaknesses"].append("Seccomp disabled - all syscalls allowed")
                                    info["weaknesses"].append("Seccomp is disabled")
                                    self.print_finding("Seccomp", "DISABLED - No syscall filtering", "critical")
                                elif int(mode) == 1:
                                    info["is_sandboxed"] = True
                                    self.print_finding("Seccomp", "Strict mode - very restricted", "info")
                                elif int(mode) == 2:
                                    info["is_sandboxed"] = True
                                    info["sandbox_types"].append("seccomp")
                                    self.print_finding("Seccomp", "Filter mode - BPF filtering active", "info")

                            if line.startswith("Seccomp_filters:"):
                                count = line.split(":")[1].strip()
                                seccomp_info["filter_count"] = int(count)
                                self.print_finding("Seccomp Filters", f"{count} filter(s) loaded")
                except Exception:
                    pass

            # Check if seccomp-bpf is available in kernel
            kernel_config_out, _, _ = self.run_command(
                "grep -E 'CONFIG_SECCOMP|CONFIG_SECCOMP_FILTER' /boot/config-$(uname -r) 2>/dev/null"
            )
            if kernel_config_out:
                seccomp_info["kernel_support"] = kernel_config_out

            # Try to detect seccomp policy weaknesses by checking allowed syscalls
            # This is limited without root, but we can try
            if seccomp_info["mode"] == 2:
                # Test some potentially dangerous syscalls
                dangerous_syscalls = ["ptrace", "process_vm_readv", "process_vm_writev"]
                seccomp_info["note"] = "Filter mode active, specific restrictions unknown without audit"

        info["seccomp"] = seccomp_info

        # =====================================================================
        # LINUX CAPABILITIES CHECK
        # =====================================================================
        self.print_finding("Checking", "Linux capabilities...")

        caps_info = {
            "effective": [],
            "permitted": [],
            "inheritable": [],
            "bounding": [],
            "ambient": [],
            "weaknesses": []
        }

        if platform.system() == "Linux":
            # Try to get capabilities using capsh
            capsh_out, _, ret = self.run_command("capsh --print 2>/dev/null")
            if ret == 0 and capsh_out:
                caps_info["raw"] = capsh_out
                for line in capsh_out.split('\n'):
                    if line.startswith("Current:"):
                        caps = line.split("=")[1].strip() if "=" in line else ""
                        caps_info["effective"] = caps.split(",") if caps else []
                    elif line.startswith("Bounding set"):
                        caps = line.split("=")[1].strip() if "=" in line else ""
                        caps_info["bounding"] = caps.split(",") if caps else []
                    elif line.startswith("Ambient set"):
                        caps = line.split("=")[1].strip() if "=" in line else ""
                        caps_info["ambient"] = caps.split(",") if caps else []

            # Alternative: read from /proc
            if os.path.exists("/proc/self/status"):
                try:
                    with open("/proc/self/status", "r") as f:
                        for line in f:
                            if line.startswith("CapEff:"):
                                caps_info["effective_hex"] = line.split(":")[1].strip()
                            elif line.startswith("CapPrm:"):
                                caps_info["permitted_hex"] = line.split(":")[1].strip()
                            elif line.startswith("CapInh:"):
                                caps_info["inheritable_hex"] = line.split(":")[1].strip()
                            elif line.startswith("CapBnd:"):
                                caps_info["bounding_hex"] = line.split(":")[1].strip()
                            elif line.startswith("CapAmb:"):
                                caps_info["ambient_hex"] = line.split(":")[1].strip()
                except Exception:
                    pass

            # Check for dangerous capabilities
            dangerous_caps = [
                "cap_sys_admin", "cap_sys_ptrace", "cap_sys_module",
                "cap_dac_override", "cap_dac_read_search", "cap_setuid",
                "cap_setgid", "cap_sys_rawio", "cap_mknod", "cap_net_admin",
                "cap_sys_chroot", "cap_sys_boot"
            ]

            effective_caps = " ".join(caps_info.get("effective", []))
            for cap in dangerous_caps:
                if cap in effective_caps.lower():
                    caps_info["weaknesses"].append(f"Has {cap}")
                    info["weaknesses"].append(f"Process has dangerous capability: {cap}")
                    self.print_warning(f"Dangerous capability: {cap}")

            if caps_info.get("effective"):
                self.print_finding("Effective Caps", ", ".join(caps_info["effective"][:5]), "medium")
            else:
                self.print_finding("Capabilities", "No special capabilities detected")

        info["capabilities"] = caps_info

        # =====================================================================
        # APPARMOR / SELINUX SANDBOX PROFILES
        # =====================================================================
        self.print_finding("Checking", "AppArmor/SELinux confinement...")

        mac_info = {
            "apparmor": {"confined": False, "profile": None},
            "selinux": {"confined": False, "context": None}
        }

        if platform.system() == "Linux":
            # Check AppArmor confinement
            if os.path.exists("/proc/self/attr/current"):
                try:
                    with open("/proc/self/attr/current", "r") as f:
                        aa_profile = f.read().strip()
                        mac_info["apparmor"]["profile"] = aa_profile
                        if aa_profile and aa_profile != "unconfined":
                            mac_info["apparmor"]["confined"] = True
                            info["is_sandboxed"] = True
                            info["sandbox_types"].append("apparmor")
                            self.print_finding("AppArmor", f"Confined by: {aa_profile}", "info")

                            # Check for weak profile indicators
                            if "complain" in aa_profile.lower():
                                info["weaknesses"].append("AppArmor in complain mode (not enforcing)")
                                self.print_warning("AppArmor in complain mode!")
                        else:
                            self.print_finding("AppArmor", "Not confined (unconfined)", "low")
                except Exception:
                    pass

            # Check SELinux context
            selinux_out, _, ret = self.run_command("id -Z 2>/dev/null")
            if ret == 0 and selinux_out and ":" in selinux_out:
                mac_info["selinux"]["context"] = selinux_out
                if "unconfined" not in selinux_out.lower():
                    mac_info["selinux"]["confined"] = True
                    info["is_sandboxed"] = True
                    info["sandbox_types"].append("selinux")
                    self.print_finding("SELinux", f"Context: {selinux_out}", "info")
                else:
                    self.print_finding("SELinux", "Running unconfined", "low")

        info["mac"] = mac_info

        # =====================================================================
        # LANDLOCK LSM (Linux 5.13+)
        # =====================================================================
        self.print_finding("Checking", "Landlock LSM...")

        landlock_info = {
            "supported": False,
            "active": False
        }

        if platform.system() == "Linux":
            # Check if Landlock is available
            landlock_check, _, ret = self.run_command(
                "grep -i landlock /sys/kernel/security/lsm 2>/dev/null"
            )
            if ret == 0 and "landlock" in landlock_check.lower():
                landlock_info["supported"] = True
                self.print_finding("Landlock", "Supported by kernel")

            # Check ABI version
            if os.path.exists("/sys/fs/landlock"):
                landlock_info["supported"] = True
                try:
                    abi_path = "/sys/fs/landlock/abi_version"
                    if os.path.exists(abi_path):
                        with open(abi_path, "r") as f:
                            landlock_info["abi_version"] = f.read().strip()
                except Exception:
                    pass

        info["landlock"] = landlock_info

        # =====================================================================
        # macOS SANDBOX (sandbox-exec, App Sandbox)
        # =====================================================================
        if platform.system() == "Darwin":
            self.print_finding("Checking", "macOS sandbox...")

            macos_sandbox = {
                "app_sandbox": False,
                "sandbox_exec": False,
                "profile": None,
                "weaknesses": []
            }

            # Check if running in App Sandbox
            # App Sandbox sets specific environment and container paths
            container_path = os.getenv("HOME", "")
            if "/Library/Containers/" in container_path:
                macos_sandbox["app_sandbox"] = True
                info["is_sandboxed"] = True
                info["sandbox_types"].append("macos_app_sandbox")
                self.print_finding("App Sandbox", "Running in App Sandbox container", "high")

                # Extract app identifier
                try:
                    parts = container_path.split("/Library/Containers/")
                    if len(parts) > 1:
                        app_id = parts[1].split("/")[0]
                        macos_sandbox["app_id"] = app_id
                        self.print_finding("Sandboxed App", app_id)
                except Exception:
                    pass

            # Check for sandbox-exec (legacy but still used)
            sandbox_exec_path, _, ret = self.run_command("which sandbox-exec 2>/dev/null")
            if ret == 0:
                macos_sandbox["sandbox_exec_available"] = True

            # Check if process is sandboxed using sandbox-info
            sandbox_check, _, ret = self.run_command(
                f"sandbox-info -p {os.getpid()} 2>/dev/null"
            )
            if ret == 0 and sandbox_check:
                if "sandboxed" in sandbox_check.lower():
                    macos_sandbox["sandbox_exec"] = True
                    info["is_sandboxed"] = True
                    macos_sandbox["sandbox_info"] = sandbox_check

            # Check for TCC (Transparency, Consent, Control) restrictions
            tcc_check, _, _ = self.run_command(
                "sqlite3 ~/Library/Application\\ Support/com.apple.TCC/TCC.db 'SELECT * FROM access LIMIT 5' 2>/dev/null"
            )
            if tcc_check:
                macos_sandbox["tcc_entries"] = True

            # Check SIP status (already in kernel_info, but relevant for sandbox)
            sip_out, _, _ = self.run_command("csrutil status 2>/dev/null")
            if sip_out and "disabled" in sip_out.lower():
                macos_sandbox["weaknesses"].append("SIP is disabled")
                info["weaknesses"].append("macOS SIP is disabled - reduced system protection")
                self.print_warning("SIP is disabled!")

            info["macos_sandbox"] = macos_sandbox

        # =====================================================================
        # SNAP CONFINEMENT
        # =====================================================================
        self.print_finding("Checking", "Snap confinement...")

        snap_info = {
            "in_snap": False,
            "confinement": None
        }

        if os.getenv("SNAP"):
            snap_info["in_snap"] = True
            snap_info["snap_name"] = os.getenv("SNAP_NAME")
            snap_info["snap_revision"] = os.getenv("SNAP_REVISION")
            info["is_sandboxed"] = True
            info["sandbox_types"].append("snap")
            self.print_finding("Snap", f"Running in Snap: {os.getenv('SNAP_NAME')}", "high")

            # Check confinement level
            snap_yaml = os.path.join(os.getenv("SNAP", ""), "meta/snap.yaml")
            if os.path.exists(snap_yaml):
                try:
                    with open(snap_yaml, "r") as f:
                        content = f.read()
                        if "confinement: strict" in content:
                            snap_info["confinement"] = "strict"
                        elif "confinement: classic" in content:
                            snap_info["confinement"] = "classic"
                            info["weaknesses"].append("Snap runs in classic confinement (no sandbox)")
                            self.print_warning("Snap has classic confinement - no sandbox!")
                        elif "confinement: devmode" in content:
                            snap_info["confinement"] = "devmode"
                            info["weaknesses"].append("Snap runs in devmode (development sandbox)")
                            self.print_warning("Snap in devmode!")
                except Exception:
                    pass

        info["snap"] = snap_info

        # =====================================================================
        # CGROUPS RESTRICTIONS
        # =====================================================================
        self.print_finding("Checking", "Cgroup restrictions...")

        cgroup_info = {
            "version": None,
            "controllers": [],
            "restrictions": {}
        }

        if platform.system() == "Linux":
            # Detect cgroup version
            if os.path.exists("/sys/fs/cgroup/cgroup.controllers"):
                cgroup_info["version"] = "v2"
                try:
                    with open("/sys/fs/cgroup/cgroup.controllers", "r") as f:
                        cgroup_info["controllers"] = f.read().strip().split()
                except Exception:
                    pass
            elif os.path.exists("/sys/fs/cgroup/cpu"):
                cgroup_info["version"] = "v1"

            self.print_finding("Cgroup Version", cgroup_info["version"] or "Unknown")

            # Check for memory limits
            mem_limit_paths = [
                "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # v1
                "/sys/fs/cgroup/memory.max"  # v2
            ]
            for path in mem_limit_paths:
                if os.path.exists(path):
                    try:
                        with open(path, "r") as f:
                            limit = f.read().strip()
                            if limit != "max" and limit != "9223372036854771712":
                                cgroup_info["restrictions"]["memory_limit"] = limit
                                self.print_finding("Memory Limit", f"{int(limit) // 1024 // 1024} MB")
                    except Exception:
                        pass
                    break

            # Check for CPU limits
            cpu_quota_paths = [
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",  # v1
                "/sys/fs/cgroup/cpu.max"  # v2
            ]
            for path in cpu_quota_paths:
                if os.path.exists(path):
                    try:
                        with open(path, "r") as f:
                            quota = f.read().strip()
                            if quota != "-1" and quota != "max":
                                cgroup_info["restrictions"]["cpu_quota"] = quota
                                self.print_finding("CPU Quota", quota)
                    except Exception:
                        pass
                    break

            # Check for PID limits
            pids_max_paths = [
                "/sys/fs/cgroup/pids/pids.max",  # v1
                "/sys/fs/cgroup/pids.max"  # v2
            ]
            for path in pids_max_paths:
                if os.path.exists(path):
                    try:
                        with open(path, "r") as f:
                            pids_max = f.read().strip()
                            if pids_max != "max":
                                cgroup_info["restrictions"]["pids_max"] = pids_max
                                self.print_finding("PID Limit", pids_max)
                    except Exception:
                        pass
                    break

        info["cgroups"] = cgroup_info

        # =====================================================================
        # SYSTEMD-NSPAWN DETECTION
        # =====================================================================
        if platform.system() == "Linux":
            if os.getenv("container") == "systemd-nspawn":
                info["is_sandboxed"] = True
                info["sandbox_types"].append("systemd-nspawn")
                self.print_finding("systemd-nspawn", "Running in systemd-nspawn container", "high")

        # =====================================================================
        # CHROOT DETECTION
        # =====================================================================
        self.print_finding("Checking", "Chroot environment...")

        chroot_info = {"in_chroot": False}

        if platform.system() == "Linux":
            # Compare root inode with init's root
            try:
                self_root = os.stat("/").st_ino
                init_root_link = os.readlink("/proc/1/root")
                # If we can't access init's root or inodes differ, might be chrooted
                if self_root != 2:  # Root inode is typically 2
                    chroot_info["in_chroot"] = True
                    chroot_info["root_inode"] = self_root
            except (OSError, PermissionError):
                pass

            # Alternative check: compare /proc/1/root with our root
            try:
                proc1_root = os.stat("/proc/1/root").st_ino
                our_root = os.stat("/").st_ino
                if proc1_root != our_root:
                    chroot_info["in_chroot"] = True
            except (OSError, PermissionError):
                # Can't access proc/1/root - might indicate isolation
                pass

        if chroot_info["in_chroot"]:
            info["is_sandboxed"] = True
            info["sandbox_types"].append("chroot")
            self.print_finding("Chroot", "Possibly running in chroot", "medium")
            info["weaknesses"].append("Chroot is weak isolation - escapable with root privileges")
        else:
            self.print_finding("Chroot", "Not detected")

        info["chroot"] = chroot_info

        # =====================================================================
        # SUMMARY
        # =====================================================================
        self.print_finding("Scanning", "Generating sandbox summary...")

        if info["is_sandboxed"]:
            self.print_finding("Sandbox Status", f"SANDBOXED ({', '.join(info['sandbox_types'])})", "high")
        else:
            self.print_finding("Sandbox Status", "NOT SANDBOXED - Running on host", "critical")

        if info["weaknesses"]:
            self.print_finding("Weaknesses Found", str(len(info["weaknesses"])), "critical")
            for weakness in info["weaknesses"][:5]:
                self.print_warning(weakness)

        self.findings["sandbox_info"] = info
        return info

    def generate_report(self, output_file: Optional[str] = None) -> str:
        """Generate the final report."""
        self.print_section("REPORT SUMMARY")

        # Calculate risk score
        risk_factors = 0

        if self.findings.get("user_info", {}).get("is_root"):
            risk_factors += 3
        if self.findings.get("kernel_info", {}).get("aslr") == "Disabled (VULNERABLE)":
            risk_factors += 2
        if self.findings.get("filesystem_info", {}).get("unusual_suid"):
            risk_factors += 2
        if self.findings.get("environment_info", {}).get("writable_path_dirs"):
            risk_factors += 2
        if self.findings.get("container_info", {}).get("docker_socket_mounted"):
            risk_factors += 3
        if self.findings.get("container_info", {}).get("privileged_mode"):
            risk_factors += 3

        ssh_files = self.findings.get("sensitive_files", {}).get("files", [])
        private_keys = [f for f in ssh_files if f.get("is_private_key")]
        if private_keys:
            risk_factors += len(private_keys)

        # Sandbox-related risk factors
        sandbox_info = self.findings.get("sandbox_info", {})
        if not sandbox_info.get("is_sandboxed"):
            risk_factors += 2  # Not sandboxed is a risk
        sandbox_weaknesses = sandbox_info.get("weaknesses", [])
        risk_factors += len(sandbox_weaknesses)  # Each weakness adds to risk

        # Seccomp disabled is critical
        seccomp_info = sandbox_info.get("seccomp", {})
        if seccomp_info.get("mode") == 0:
            risk_factors += 2

        # Dangerous capabilities
        caps_info = sandbox_info.get("capabilities", {})
        if caps_info.get("weaknesses"):
            risk_factors += len(caps_info.get("weaknesses", []))

        # Print summary
        self.print_finding("Total Findings Categories", str(len(self.findings)))

        risk_level = "LOW"
        if risk_factors >= 5:
            risk_level = "HIGH"
        elif risk_factors >= 3:
            risk_level = "MEDIUM"

        severity = "critical" if risk_level == "HIGH" else "medium" if risk_level == "MEDIUM" else "info"
        self.print_finding("Overall Risk Level", f"{risk_level} (Score: {risk_factors})", severity)

        # Add metadata
        self.findings["_metadata"] = {
            "scan_time": datetime.datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "risk_score": risk_factors,
            "risk_level": risk_level
        }

        # Output
        if self.json_output:
            report = json.dumps(self.findings, indent=2, default=str)
        else:
            report = self._generate_text_report()

        if output_file:
            with open(output_file, "w") as f:
                f.write(report)
            print(f"\n{Colors.GREEN}[+] Report saved to: {output_file}{Colors.RESET}")

        return report

    def _generate_text_report(self) -> str:
        """Generate a text-based report."""
        lines = [
            "=" * 60,
            "RED ALERT - System Discovery Report",
            f"Generated: {datetime.datetime.now().isoformat()}",
            f"Hostname: {socket.gethostname()}",
            "=" * 60,
            ""
        ]

        for section, data in self.findings.items():
            if section.startswith("_"):
                continue
            lines.append(f"\n[{section.upper().replace('_', ' ')}]")
            lines.append("-" * 40)

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, (list, dict)):
                        lines.append(f"  {key}: {len(value) if isinstance(value, list) else 'complex'}")
                    else:
                        lines.append(f"  {key}: {str(value)[:100]}")
            else:
                lines.append(f"  {str(data)[:200]}")

        return "\n".join(lines)

    def run(self) -> Dict[str, Any]:
        """Execute all discovery modules."""
        if not self.json_output:
            print(f"""
{Colors.RED}
  ____          _      _    _           _
 |  _ \\ ___  __| |    / \\  | | ___ _ __| |_
 | |_) / _ \\/ _` |   / _ \\ | |/ _ \\ '__| __|
 |  _ <  __/ (_| |  / ___ \\| |  __/ |  | |_
 |_| \\_\\___|\\__,_| /_/   \\_\\_|\\___|_|   \\__|
{Colors.RESET}
{Colors.YELLOW}System Discovery Tool for Authorized Security Testing{Colors.RESET}
{Colors.CYAN}Only use on systems you have permission to test!{Colors.RESET}
""")

        # Run all discovery modules
        self.get_system_info()
        self.get_kernel_info()
        self.get_user_info()
        self.get_network_info()
        self.get_process_info()
        self.get_filesystem_info()
        self.find_sensitive_files()
        self.get_environment_info()
        self.get_scheduled_tasks()
        self.get_installed_software()
        self.check_container_environment()
        self.check_sandbox_environment()

        return self.findings


def main():
    parser = argparse.ArgumentParser(
        description="Red Alert - System Discovery Tool for Authorized Security Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 red-alert.py                    # Run full scan with colored output
  python3 red-alert.py --quick            # Run quick scan (skip intensive checks)
  python3 red-alert.py --json             # Output as JSON
  python3 red-alert.py -o report.txt      # Save report to file
  python3 red-alert.py --json -o scan.json  # Save JSON report

DISCLAIMER: Only use on systems you own or have explicit authorization to test.
"""
    )

    parser.add_argument(
        "-o", "--output",
        help="Save report to specified file"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode - skip intensive file searches"
    )

    args = parser.parse_args()

    try:
        scanner = RedAlert(json_output=args.json, quick_mode=args.quick)
        findings = scanner.run()
        report = scanner.generate_report(args.output)

        if args.json and not args.output:
            print(report)

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] Scan interrupted by user{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}[!] Error: {e}{Colors.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
