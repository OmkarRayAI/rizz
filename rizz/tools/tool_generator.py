import os
import importlib
import inspect
from typing import List, Dict, Any


class AutomatedToolGenerator:
    def __init__(self, tools_folder_path: str = None, external_folder_path: str = None, debug: bool = False):
        """
        Auto-discover tools in the given folder(s).
        tools_folder_path: relative or absolute path to the internal tools directory.
                           If None we auto-detect relative to this file's location.
        external_folder_path: relative or absolute path to external tools directory.
        debug: If True, print debug information during tool discovery.
        """
        self.debug = debug
        
        if tools_folder_path is None:
            # Auto-detect "./tools" relative to where this file is located
            current_file_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Check if we're inside a tools folder
            if os.path.basename(current_file_dir) == "tools":
                self.tools_folder_path = current_file_dir
                self.is_inside_tools = True
            else:
                # Look for tools folder next to this file
                self.tools_folder_path = os.path.join(current_file_dir, "tools")
                self.is_inside_tools = False
        else:
            self.tools_folder_path = tools_folder_path
            self.is_inside_tools = False

        self.external_folder_path = external_folder_path
        self.available_tools: Dict[str, Any] = {}
        
        # Discover internal tools
        self._discover_tools()
        
        # Discover external tools if path provided
        if external_folder_path:
            self._discover_external_tools()
        
        # Print discovered tools if debug is enabled
        if self.debug:
            print(f"DEBUG: Discovered {len(self.available_tools)} tools:")
            for tool_name in self.available_tools.keys():
                print(f"  - {tool_name} (from {self.available_tools[tool_name]['file_path']})")

    # ------------------------------------------------------------------
    # discovery - internal tools
    # ------------------------------------------------------------------
    def _discover_tools(self):
        if self.debug:
            print(f"DEBUG: Looking for internal tools in: {self.tools_folder_path}")
            print(f"DEBUG: Path exists: {os.path.exists(self.tools_folder_path)}")
        
        if not os.path.exists(self.tools_folder_path):
            # Don't print warning if we have external tools
            if not self.external_folder_path:
                print(f"Warning: Tools folder '{self.tools_folder_path}' not found")
            return

        excluded = {"__init__.py", "tool_generator.py", "toolgen.py", "__pycache__"}
        
        # Determine the package path for internal tools
        # Since tool_generator.py is in rizz/, and tools is rizz/tools/
        # We need to import as "rizz.tools.module_name"
        import sys

        # Get the parent directory of tools folder (should be rizz)
        parent_dir = os.path.dirname(self.tools_folder_path)
        package_name = os.path.basename(parent_dir)  # Should be "rizz"
        
        # Add parent to sys.path if needed
        grandparent_dir = os.path.dirname(parent_dir)
        if grandparent_dir not in sys.path:
            sys.path.insert(0, grandparent_dir)

        for filename in os.listdir(self.tools_folder_path):
            if filename.endswith(".py") and filename not in excluded and not filename.startswith("_"):
                module_name = filename[:-3]  # strip ".py"
                if self.debug:
                    print(f"DEBUG: Trying to load internal tool: {module_name}")
                try:
                    # Import as part of the package to support relative imports
                    full_module_path = f"{package_name}.tools.{module_name}"
                    module = importlib.import_module(full_module_path)

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if name.endswith("Tool") and hasattr(obj, "get_tool"):
                            # ensure it is defined in this module
                            if obj.__module__ != module.__name__:
                                continue
                            if self.debug:
                                print(f"DEBUG: Found internal tool class: {name}")
                            # store by class name (the 'name' variable from inspect.getmembers)
                            self.available_tools[name] = {
                                "class": obj,
                                "module_name": module_name,
                                "file_path": os.path.join(self.tools_folder_path, filename),
                            }

                except Exception as e:
                    print(f"Warning: failed to load {module_name}: {e}")

    # ------------------------------------------------------------------
    # discovery - external tools
    # ------------------------------------------------------------------
    def _discover_external_tools(self):
        if not os.path.exists(self.external_folder_path):
            print(f"Warning: External tools folder '{self.external_folder_path}' not found")
            return

        excluded = {"__init__.py", "tool_generator.py", "toolgen.py", "__pycache__"}
        
        # Add the external folder to sys.path if not already there
        import sys
        abs_external_path = os.path.abspath(self.external_folder_path)
        if abs_external_path not in sys.path:
            sys.path.insert(0, abs_external_path)

        for filename in os.listdir(self.external_folder_path):
            if filename.endswith(".py") and filename not in excluded and not filename.startswith("_"):
                module_name = filename[:-3]  # strip ".py"
                try:
                    # Import directly by module name (the directory is in sys.path)
                    # This avoids relative import issues in package contexts
                    module = importlib.import_module(module_name)

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if name.endswith("Tool") and hasattr(obj, "get_tool"):
                            # ensure it is defined in this module
                            if obj.__module__ != module.__name__:
                                continue
                            # store by class name (the 'name' variable from inspect.getmembers)
                            self.available_tools[name] = {
                                "class": obj,
                                "module_name": module_name,
                                "file_path": os.path.join(self.external_folder_path, filename),
                            }

                except Exception as e:
                    print(f"Warning: failed to load external {module_name}: {e}")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _get_init_params(self, cls) -> list[str]:
        try:
            sig = inspect.signature(cls.__init__)
            return [p.name for p in sig.parameters.values() if p.name != "self"]
        except Exception:
            return ["extra_info", "name"]  # fallback

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------
    def generate_tools(self, tools_list: List[Dict[str, Any]]) -> List[Any]:
        """
        tools_list example:
        [
            {"class": "ArcadeGitAgentTool", "name": "Github_Agent", "apikey": "...", ...},
            {"class": "ExternalGmailTool", "name": "Gmail_Agent", ...},
            {"class": "ExternalLlamaIndexQueryTool", "name": "Query_Tool", "kwargs": {...}},
        ]
        """
        generated = []

        for cfg in tools_list:
            key = cfg.get("class")
            if not key or key not in self.available_tools:
                print(f"Warning: '{key}' not found or missing in config")
                continue

            tool_cls = self.available_tools[key]["class"]
            required = self._get_init_params(tool_cls)

            # collect init arguments from cfg
            # Check if there's a nested 'kwargs' dict that should be merged in
            if "kwargs" in cfg and isinstance(cfg["kwargs"], dict):
                # Merge the nested kwargs with top-level params
                init_kwargs = {k: v for k, v in cfg.items() if k in required and k != "kwargs"}
                # Add kwargs from the nested dict
                for k, v in cfg["kwargs"].items():
                    if k in required:
                        init_kwargs[k] = v
            else:
                init_kwargs = {k: v for k, v in cfg.items() if k in required}
            
            init_kwargs.setdefault("extra_info", f"Auto-generated {key}")
            init_kwargs.setdefault("name", cfg.get("name", key))

            try:
                instance = tool_cls(**init_kwargs)
                generated.append(instance.get_tool())
            except Exception as e:
                print(f"Error instantiating {key}: {e}")

        return generated

    def list_available(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {
                "class_name": v["class"].__name__,
                "required_params": self._get_init_params(v["class"]),
                "file": v["file_path"],
            }
            for k, v in self.available_tools.items()
        }


# ------------------------------------------------------------------
# convenience wrapper (updated to accept external path)
# ------------------------------------------------------------------
def ToolGenerator(tools_list: List[Dict[str, Any]], tools_folder: str = None, debug: bool = False) -> List[Any]:
    """
    Generate tools from internal and/or external folders.
    
    Args:
        tools_list: List of tool configurations with "class" names
        tools_folder: Path to external tools folder (optional)
        debug: If True, print debug information during tool discovery
    
    Returns:
        List of generated tool instances
    """
    # Create generator with both internal (auto-detected) and external paths
    generator = AutomatedToolGenerator(None, tools_folder, debug)
    return generator.generate_tools(tools_list)