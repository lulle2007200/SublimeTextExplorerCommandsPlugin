import sublime
import sublime_plugin

import tempfile
import requests
import pathlib
import zipfile
import datetime
import os
import sys
import functools
import subprocess
import json
from typing import Optional

package_name  = "SublimeExplorerIntegration"
settings_path = "SublimeExplorerIntegration.sublime-settings"

def update_last_update_check_ts():
	settings = sublime.load_settings(settings_path)
	update_check_ts = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())
	settings.set("last_update_check_ts", update_check_ts)
	sublime.save_settings(settings_path)

class Installer:
	repo         = "SublimeTextExplorerCommands"
	owner        = "lulle2007200"
	package_name = "SublimeText-06928381-4738-4766-a51d-7eb23471fbaa"
	
	def __init__(self):
		self.tmp_dir      = pathlib.Path(tempfile.mkdtemp())
		self.extract_dir  = self.tmp_dir / "extract"
		self.archive      = self.tmp_dir / "package.zip"
		self.install_dir  = self.get_sublime_install_dir()
		self.root_cert    = self.extract_dir / "root_ca.cer"
		self.package      = self.extract_dir / "package.msix"
		self.external     = self.extract_dir / "external"
		self.release_info = None #type: Optional[dict]

	# Must be called before calling any other member except 
	def load_release_info(self):
		if not self.release_info:
			url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/latest"
			with requests.get(url) as req:
				req.raise_for_status()
				self.release_info = req.json()

	def get_release_timestamp(self) -> int:
		self.load_release_info()
		published_at = datetime.datetime.fromisoformat(self.release_info["published_at"].replace("Z","+00:00"))
		return int(published_at.timestamp())

	def get_release_url(self) -> str:
		self.load_release_info()
		return self.release_info["assets"][0]["browser_download_url"]

	def get_sublime_install_dir(self) -> pathlib.Path:
		exe_path = pathlib.Path(sys.executable).parent
		return exe_path

	def download_package(self) -> None:
		self.load_release_info()
		url = self.get_release_url()
		with requests.get(url, stream=True) as req:
			req.raise_for_status()
			with open(self.archive, "wb") as f:
				for chunk in req.iter_content(chunk_size=8192):
					if chunk:
						f.write(chunk)

	def get_installed_release_ts(self) -> int:
		settings = sublime.load_settings(settings_path)
		if not self.is_installed():
			# If msix package not installed, reset saved version
			settings.erase("installed_release_ts")
			sublime.save_settings(settings_path)
		ts = settings.get("installed_release_ts")
		return ts

	def make_launch_subl_cmd(self, command=None, **kwargs) -> str:
		dquote = "\\\\\"\""

		args = ""
		if command:
			cmd_args = json.dumps(kwargs).replace("\"", dquote)
			args = f"--command \"\"{command} {cmd_args}\"\""

		cmd = (f"$s = New-Object -ComObject Shell.Application;",
		       f"$s.ShellExecute(''subl.exe'',",
		       f"\"{args}\",",
		       f"''{self.install_dir}'')")

		return "".join(cmd)

	def make_uninstall_cmd(self) -> str:
		# TODO: When installing, record extracted files and certificates and remove those aswell

		close_sublime_cmd      = f"&\"{self.install_dir}/subl.exe\" --command exit"
		remove_package_cmd     = f"Get-AppxPackage -Name {self.package_name} | Remove-AppxPackage"

		uninstall_success_cmd  = self.make_launch_subl_cmd("sublime_explorer_integration_uninstall_done", result=0)
		uninstall_failure_cmd  = self.make_launch_subl_cmd("sublime_explorer_integration_uninstall_done", result=-1)

		# cmd = ";".join([close_sublime_cmd,
		#                 remove_package_cmd])

		cmd = ";".join([remove_package_cmd])

		cmd = f"try {{{cmd};{uninstall_success_cmd}}}catch{{{uninstall_failure_cmd}}}"

		return cmd

	def make_install_cmd(self) -> str:
		ts = self.get_release_timestamp()

		close_sublime_cmd   = f"&\"{self.install_dir}/subl.exe\" --command \"exit\""
		install_cert_cmd    = f"Import-Certificate -FilePath \"{self.root_cert}\" -CertStoreLocation Cert:\\LocalMachine\\Root"
		copy_external_cmd   = f"Copy-Item -Path \"{self.external}/*\" -Destination \"{self.install_dir}\" -Recurse -Force"
		remove_package_cmd  = f"Get-AppxPackage -Name {self.package_name} | Remove-AppxPackage"
		install_package_cmd = f"Add-AppxPackage -Path \"{self.package}\" -ExternalLocation \"{self.install_dir}\""

		update_success_cmd = self.make_launch_subl_cmd("sublime_explorer_integration_install_done", result=0, release_ts=ts)
		update_failure_cmd = self.make_launch_subl_cmd("sublime_explorer_integration_install_done", result=-1, release_ts=ts)

		# cmd = ";".join([close_sublime_cmd,
		#                 install_cert_cmd,
		#                 copy_external_cmd,
		#                 remove_package_cmd,
		#                 install_package_cmd])

		cmd = ";".join([install_cert_cmd,
		                copy_external_cmd,
		                remove_package_cmd,
		                install_package_cmd])

		cmd = f"try {{{cmd};{update_success_cmd}}}catch{{{update_failure_cmd}}}"

		return cmd

	def extract_package(self) -> None:
		pathlib.Path.mkdir(self.extract_dir, exist_ok=True)
		with zipfile.ZipFile(self.archive, 'r') as f:
			f.extractall(self.extract_dir)

	def update_available(self) -> bool:
		return self.get_installed_release_ts() < self.get_release_timestamp()

	def run_hidden(self, cmd, **kwargs) -> subprocess.Popen:
		si = subprocess.STARTUPINFO()
		si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

		return subprocess.Popen(cmd, startupinfo=si, **kwargs)

	def run_elevated_hidden(self, cmd: str) -> subprocess.Popen:
		cmd = cmd.replace("\"", "\"\"\"")
		arg_list = f"'--headless powershell.exe -WindowStyle Hidden -Command {cmd}'"
		# arg_list = f"'-NoExit -Command {cmd}'"
		cmd_list = ["powershell.exe", 
		            "-WindowStyle", "Hidden", 
		            # "-NoExit", "-Command", "Start-Process", "powershell.exe", 
		            "-Command", "Start-Process", "conhost.exe", 
		            "-Verb", "RunAs", 
		            "-ArgumentList", arg_list]

		return self.run_hidden(cmd_list)

	def is_installed(self) -> bool:
		cmd = f"powershell.exe -WindowStyle Hidden -Command Get-AppxPackage -Name {self.package_name}"

		p = self.run_hidden(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		stdout, stderr = p.communicate()
		if p.returncode != 0:
			raise RuntimeError("Get-AppxPackage failed")

		settings = sublime.load_settings(settings_path)

		appx_installed = stdout.strip() != ""
		return appx_installed

	def uninstall(self) -> None:
		cmd = self.make_uninstall_cmd()
		self.run_elevated_hidden(cmd)

	def install(self) -> None:
		self.download_package()
		self.extract_package()

		cmd = self.make_install_cmd()
		self.run_elevated_hidden(cmd)

class SublimeExplorerIntegrationInstallCommand(sublime_plugin.ApplicationCommand) :
	def is_enabled(self):
		return True

	def on_select(self, idx):
		try:
			if idx == 0:
				self.i.install()
		except Exception:
			sublime.run_command("sublime_explorer_integration_install_done", {"result":-1})
			raise

	def async_run(self):
		try:
			self.i = Installer()
			self.i.load_release_info()
			window = sublime.active_window()
			items = ["Install", "Cancel"]
			placeholder = "Continue with installation?"
			if self.i.is_installed():
				items[0] = "Reinstall"
				placeholder = "Already installed. Continue with reinstallation?"
			window.show_quick_panel(items, self.on_select, placeholder=placeholder)
		except Exception:
			sublime.run_command("sublime_explorer_integration_install_done", {"result":-1})
			raise

	def run(self, run_async=True):
		if run_async:
			sublime.set_timeout_async(self.async_run)
		else:
			self.async_run()

class SublimeExplorerIntegrationUninstallCommand(sublime_plugin.ApplicationCommand) :
	def is_enabled(self):
		return True

	def on_select(self, idx):
		try:
			if idx == 0:
				self.i.uninstall()
		except Exception:
			sublime.run_command("sublime_explorer_integration_uninstall_done", {"result":-1})
			raise

	def async_run(self):
		try:
			self.i = Installer()
			window = sublime.active_window()
			cb = self.on_select
			items = ["Uninstall", "Cancel"]
			placeholder = "Continue with deinstallation?"
			if not self.i.is_installed():
				items = ["Ok"]
				placeholder = "Not installed."
				cb = None
			window.show_quick_panel(items, cb, placeholder=placeholder)
		except Exception:
			sublime.run_command("sublime_explorer_integration_uninstall_done", {"result":-1})
			raise

	def run(self, run_async=False):
		if run_async:
			sublime.set_timeout_async(self.async_run)
		else:
			self.async_run()

class SublimeExplorerIntegrationUpdateCommand(sublime_plugin.ApplicationCommand) :
	def is_enabled(self):
		return True

	def on_select(self, idx):
		try:
			if idx == 0:
				self.i.install()
		except Exception:
			sublime.run_command("sublime_explorer_integration_install_done", {"result":-1})
			raise

	def async_run(self, silent):
		try:
			self.i = Installer()
			self.i.load_release_info()

			settings = sublime.load_settings(settings_path)

			items = ["Update", "Cancel"]
			cb = self.on_select
			placeholder = "Update available. Update now?"
			update_available = self.i.update_available()

			if not update_available:
				items = ["Ok"]
				cb = None
				placeholder = "No Update available."


			update_last_update_check_ts()

			if update_available or not silent:
				window = sublime.active_window()
				window.show_quick_panel(items, cb, placeholder=placeholder)
		except Exception:
			sublime.run_command("sublime_explorer_integration_install_done", {"result":-1})
			raise

	def run(self, run_async=False, silent=False):
		if run_async:
			sublime.set_timeout_async(lambda: self.async_run(silent=False))
		else:
			self.async_run(silent=silent)

class SublimeExplorerIntegrationInstallDoneCommand(sublime_plugin.ApplicationCommand) :
	def is_enabled(self):
		return True

	def run(self, result, release_ts=0):
		items = ["Ok"]
		window = sublime.active_window()
		placeholder = "Install succeeded."
		if result < 0:
			placeholder = "Install failed."

		i = Installer()
		settings = sublime.load_settings(settings_path)
		if result == 0:
			settings.set("installed_release_ts", release_ts)
		else:
			settings.erase("installed_release_ts")
		sublime.save_settings(settings_path)

		window.run_command("hide_overlay")
		window.show_quick_panel(items, None, placeholder=placeholder)

class SublimeExplorerIntegrationUninstallDoneCommand(sublime_plugin.ApplicationCommand) :
	def is_enabled(self):
		return True


	def run(self, result):
		items = ["Ok"]
		window = sublime.active_window()
		placeholder = "Uninstall succeeded."
		if result < 0:
			placeholder = "Uninstall failed."

		if result == 0:
			i = Installer()
			settings = sublime.load_settings(settings_path)
			settings.erase("installed_release_ts")
			sublime.save_settings(settings_path)

		window.run_command("hide_overlay")
		window.show_quick_panel(items, None, placeholder=placeholder)

def plugin_loaded():
	settings = sublime.load_settings(settings_path)

	try:
		from package_control import events
		if events.install(package_name):
			i = Installer()
			installed = i.is_installed()
			print(installed)
			if not installed:
				# NOTE: Command not available via run_command right after install
				# sublime.run_command("sublime_explorer_integration_install")
				cmd = SublimeExplorerIntegrationInstallCommand()
				cmd.run()
			return
	except ImportError:
		return

	if settings.get("auto_updates"):
		last_update_check_ts = settings.get("last_update_check_ts")
		update_check_interval = settings.get("update_check_interval")
		now_ts = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())

		if now_ts > last_update_check_ts + update_check_interval:
			sublime.run_command("sublime_explorer_integration_update")

def plugin_unloaded():
	try:
		from package_control import events
		if events.remove(package_name):
			i = Installer()
			if i.is_installed():
				sublime.run_command("sublime_explorer_integration_uninstall")
			return
	except ImportError:
		return