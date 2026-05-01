"""
Docker Container Manager
Handles starting, stopping, and monitoring Docker containers for challenges
"""

try:
    import docker  # type: ignore[import]
    DOCKER_AVAILABLE = True
except ImportError as e:
    print(f"[ERROR] Failed to import docker module: {e}", flush=True)
    print("[ERROR] Please install docker package: pip install docker", flush=True)
    DOCKER_AVAILABLE = False
    docker = None  # type: ignore

import socket
import os
import platform
from typing import Dict, Any
import json

class DockerManager:
    """Manages Docker containers for challenges"""
    
    # Container port mapping cache
    _container_ports: Dict[str, int] = {}
    _client = None
    _initialized_once = False
    _next_port = 16000  # Shifted up to avoid conflicts and TIME_WAIT issues
    
    @staticmethod
    def _initialize_client():
        """Initialize Docker client - allows retries if daemon not available"""
        if not DOCKER_AVAILABLE:
            print("[ERROR] Docker module is not available. Please install it with: pip install docker", flush=True)
            DockerManager._initialized_once = True
            return False
        
        try:
            client = docker.from_env()
            client.ping()
            DockerManager._client = client
            DockerManager._initialized_once = True
            print("[INFO] Docker client initialized successfully", flush=True)
            return True
        except Exception as e:
            print(f"[DEBUG] Docker connection attempt failed: {str(e)}", flush=True)
            DockerManager._client = None
            DockerManager._initialized_once = True
            return False

    
    @staticmethod
    def _get_docker_client():
        """Get Docker client, returning None if daemon not available"""
        if not DOCKER_AVAILABLE:
            return None
        
        # Always try to connect if we don't have a valid client
        if DockerManager._client is None:
            success = DockerManager._initialize_client()
            if not success:
                return None
        
        return DockerManager._client
    
    @staticmethod
    def _is_port_available(port: int) -> bool:
        """Check if a port is actually available for binding"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', port))
            sock.close()
            return True
        except (OSError, socket.error):
            return False

    @staticmethod
    def _get_available_port(start_port: int = 16000, end_port: int = 18000) -> tuple:
        """Find two consecutive available ports in the specified range
        
        Returns:
            (port1, port2) - two consecutive available ports
        """
        # Start from the next port we should try
        port = max(DockerManager._next_port, start_port)
        
        while port + 1 < end_port:
            # Check if both ports are available
            if DockerManager._is_port_available(port) and DockerManager._is_port_available(port + 1):
                DockerManager._next_port = port + 2
                print(f"[DEBUG] Found available port pair: {port}, {port + 1}", flush=True)
                return (port, port + 1)
            
            port += 2
            
            # If we've searched too far, wrap around
            if port >= end_port:
                port = start_port
                if port >= end_port - 1:
                    raise Exception(f"No available port pairs found in range {start_port}-{end_port}")
        
        raise Exception(f"No available port pairs found in range {start_port}-{end_port}")
    
    @staticmethod
    def start_container(docker_image: str, challenge_id: int, user_id: int, is_preview: bool = False) -> Dict[str, Any]:
        """
        Start a Docker container using the library with CLI fallback for maximum reliability.
        """
        import time
        import subprocess
        
        try:
            # 1. Initialize client/check availability
            client = DockerManager._get_docker_client()
            
            # 2. Get available ports
            port, port_alt = DockerManager._get_available_port()
            container_name = f"challenge_{challenge_id}_user_{user_id}_{port}"
            
            # 3. Cleanup existing
            print(f"[INFO] Cleaning up container {container_name} if exists...", flush=True)
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            
            if is_preview:
                # Force cleanup of any older preview containers for this same challenge
                try:
                    find_cmd = ["docker", "ps", "-a", "--filter", f"name=challenge_{challenge_id}_user_0_", "--format", "{{.Names}}"]
                    proc = subprocess.run(find_cmd, capture_output=True, text=True)
                    for old_name in proc.stdout.splitlines():
                        if old_name.strip():
                            subprocess.run(["docker", "rm", "-f", old_name.strip()], capture_output=True)
                except: pass

            # 4. Determine exposed ports (using CLI for robustness)
            exposed_ports = {}
            try:
                inspect_cmd = ["docker", "inspect", "--format", "{{json .Config.ExposedPorts}}", docker_image]
                proc = subprocess.run(inspect_cmd, capture_output=True, text=True)
                if proc.returncode == 0 and proc.stdout.strip() and proc.stdout.strip() != "null":
                    exposed_ports = json.loads(proc.stdout.strip())
            except: pass

            # 5. Build port mapping
            port_map = {}
            if not exposed_ports:
                port_map = {'5000/tcp': port}
            else:
                for exp_port in exposed_ports:
                    if '8000' in exp_port:
                        port_map[exp_port] = port_alt
                    else:
                        if port not in port_map.values():
                            port_map[exp_port] = port

            # 6. Start container via CLI
            run_cmd = ["docker", "run", "-d", "--name", container_name]
            for cont_port, host_port in port_map.items():
                # Map only to localhost for security
                run_cmd.extend(["-p", f"127.0.0.1:{host_port}:{cont_port.split('/')[0]}"])
            run_cmd.append(docker_image)
            
            print(f"[INFO] Running Docker CLI: {' '.join(run_cmd)}", flush=True)
            proc = subprocess.run(run_cmd, capture_output=True, text=True)
            
            if proc.returncode != 0:
                raise Exception(f"Docker run failed: {proc.stderr}")
            
            container_id = proc.stdout.strip()
            
            # 7. Determine which host port to link to
            actual_port = port
            if any('8000' in p for p in port_map.keys()):
                actual_port = port_alt
            
            # 8. Health check
            print(f"[INFO] Waiting for port {actual_port} to become responsive...", flush=True)
            for _ in range(15):
                try:
                    import socket as s
                    with s.socket(s.AF_INET, s.SOCK_STREAM) as sock:
                        sock.settimeout(0.5)
                        if sock.connect_ex(('127.0.0.1', actual_port)) == 0:
                            print(f"[INFO] Container is responsive on port {actual_port}", flush=True)
                            break
                except: pass
                time.sleep(0.5)

            return {
                "success": True,
                "container_id": container_id,
                "container_name": container_name,
                "port": actual_port,
                "message": f"Successfully started on port {actual_port}"
            }
            
        except Exception as e:
            print(f"[ERROR] Start failed: {str(e)}", flush=True)
            return {
                "success": False,
                "error": str(e),
                "container_id": None,
                "container_name": None,
                "port": None
            }
    
    @staticmethod
    def stop_container(container_id: str) -> Dict[str, Any]:
        """
        Stop a running Docker container with CLI fallback for maximum reliability.
        
        Args:
            container_id: Container ID
            
        Returns:
            {
                "success": bool,
                "message": str,
                "error": str (if failed)
            }
        """
        import subprocess
        try:
            print(f"[DOCKER] Attempting to stop container: {container_id[:12]}...", flush=True)
            
            # Method 1: Try using the Docker library if available
            client = DockerManager._get_docker_client()
            library_success = False
            
            if client:
                try:
                    container = client.containers.get(container_id)
                    print(f"[DOCKER] Container found via library: {container.name} (Status: {container.status})", flush=True)
                    
                    if container.status == 'running':
                        print(f"[DOCKER] Stopping container (graceful shutdown via library)...", flush=True)
                        container.stop(timeout=5)
                    
                    print(f"[DOCKER] Removing container via library...", flush=True)
                    container.remove(force=True)
                    library_success = True
                    print(f"[DOCKER] Library termination successful", flush=True)
                except Exception as e:
                    print(f"[DOCKER] Library stop/remove failed: {str(e)}, falling back to CLI...", flush=True)
            
            # Method 2: Always try CLI as fallback or verification (more robust on Windows)
            if not library_success:
                print(f"[DOCKER] Running Docker CLI: docker rm -f {container_id[:12]}", flush=True)
                proc = subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True)
                
                if proc.returncode == 0:
                    print(f"[DOCKER] CLI termination successful", flush=True)
                else:
                    # Check if it failed because it doesn't exist (which is also a kind of success for stopping)
                    if "No such container" in proc.stderr:
                        print(f"[DOCKER] Container already gone", flush=True)
                    else:
                        print(f"[DOCKER] CLI termination failed: {proc.stderr}", flush=True)
                        if not library_success:
                            return {
                                "success": False,
                                "error": f"Failed to stop container: {proc.stderr}"
                            }

            # Clear port cache
            DockerManager._container_ports.pop(container_id, None)
            
            return {
                "success": True,
                "message": f"Container {container_id[:12]} stopped and removed successfully"
            }
            
        except Exception as e:
            print(f"[DOCKER] Error stopping container: {str(e)}", flush=True)
            return {
                "success": False,
                "error": f"Failed to stop container: {str(e)}"
            }
    
    @staticmethod
    def get_container_status(container_id: str) -> Dict[str, Any]:
        """
        Get the status of a Docker container with CLI fallback for reliability.
        """
        try:
            # Method 1: Library
            client = DockerManager._get_docker_client()
            if client:
                try:
                    container = client.containers.get(container_id)
                    container.reload()
                    return {
                        "status": container.status,
                        "success": True
                    }
                except Exception:
                    pass
            
            # Method 2: CLI fallback
            import subprocess
            proc = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", container_id],
                capture_output=True, text=True
            )
            if proc.returncode == 0:
                return {
                    "status": proc.stdout.strip(),
                    "success": True
                }
            
            return {
                "status": "not_found",
                "success": False,
                "error": "Container not found"
            }
        except Exception as e:
            return {
                "status": "error",
                "success": False,
                "error": str(e)
            }

    @staticmethod
    def get_container_stats(container_id: str) -> Dict[str, Any]:
        """
        Get real-time stats for a container (CPU, Memory, IO, PIDs, etc.)
        
        Returns:
            {
                "cpu_percent": str,
                "memory_usage": str,
                "memory_percent": str,
                "net_io": str,
                "block_io": str,
                "pids": str,
                "image": str,
                "name": str
            }
        """
        import subprocess
        import json
        
        stats = {
            "cpu_percent": "0.00%",
            "memory_usage": "0B / 0B",
            "memory_percent": "0.00%",
            "net_io": "0B / 0B",
            "block_io": "0B / 0B",
            "pids": "0",
            "image": "N/A",
            "name": "N/A"
        }
        
        try:
            # Use CLI for stats as it's formatted perfectly for display
            proc = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_id],
                capture_output=True, text=True, timeout=5
            )
            
            if proc.returncode == 0 and proc.stdout.strip():
                # Some versions of docker stats return multiple lines or malformed JSON if not handled
                output = proc.stdout.strip().split('\n')[0]
                data = json.loads(output)
                stats.update({
                    "cpu_percent": data.get("CPUPerc", "0.00%"),
                    "memory_usage": data.get("MemUsage", "0B / 0B"),
                    "memory_percent": data.get("MemPerc", "0.00%"),
                    "net_io": data.get("NetIO", "0B / 0B"),
                    "block_io": data.get("BlockIO", "0B / 0B"),
                    "pids": str(data.get("PIDs", "0")),
                    "name": data.get("Name", "N/A")
                })
                
                # Get Image name via inspect
                inspect_proc = subprocess.run(
                    ["docker", "inspect", "-f", "{{.Config.Image}}", container_id],
                    capture_output=True, text=True
                )
                if inspect_proc.returncode == 0:
                    stats["image"] = inspect_proc.stdout.strip()

            return stats
        except Exception as e:
            print(f"[DOCKER] Error getting stats for {container_id}: {str(e)}", flush=True)
            return stats
    
    @staticmethod
    def list_containers(challenge_id: int | None = None) -> Dict[str, Any]:
        """
        List containers, optionally filtered by challenge ID
        
        Args:
            challenge_id: Optional challenge ID to filter
            
        Returns:
            {
                "success": bool,
                "containers": list of container info,
                "error": str (if failed)
            }
        """
        try:
            client = docker.from_env()
            containers = client.containers.list(all=True)
            
            result = []
            for container in containers:
                if challenge_id and f"challenge_{challenge_id}" not in container.name:
                    continue
                    
                result.append({
                    "id": container.id[:12],
                    "name": container.name,
                    "status": container.status,
                    "image": container.image.tags if container.image.tags else "unknown"
                })
            
            return {
                "success": True,
                "containers": result
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to list containers: {str(e)}",
                "containers": []
            }

    @staticmethod
    def list_images() -> Dict[str, Any]:
        """
        List all available Docker images
        
        Returns:
            {
                "success": bool,
                "images": list of image names with tags,
                "error": str (if failed)
            }
        """
        try:
            client = docker.from_env()
            images = client.images.list()
            
            result = []
            for image in images:
                # Get all tags for this image
                if image.tags:
                    for tag in image.tags:
                        result.append(tag)
                else:
                    # Images without tags show as <none>:<none>
                    result.append(f"{image.id[:12]}")
            
            # Sort and remove duplicates
            result = sorted(list(set(result)))
            
            return {
                "success": True,
                "images": result
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to list Docker images: {str(e)}",
                "images": []
            }


# Initialize Docker client when module is imported
print("[INFO] Docker Manager module loaded", flush=True)

if not DOCKER_AVAILABLE:
    print("[ERROR] Docker Python module is NOT available", flush=True)
    print("[ERROR] Install it with: pip install docker", flush=True)
else:
    try:
        success = DockerManager._initialize_client()
        if not success:
            print("[WARNING] Docker daemon is not running yet (will retry on first use)", flush=True)
    except Exception as e:
        print(f"[WARNING] Docker initialization failed (non-critical): {str(e)}", flush=True)
        print("[INFO] App will continue but Docker features will be unavailable", flush=True)

