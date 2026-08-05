# Hosts & terminal

## Add a host

1. Click **+** → **Add host**.
2. Fill name, address, port, username, password (or SSH key path).
3. Choose connection type:
   - **SSH**: Linux / most Unix systems
   - **WinRM**: Windows (transport, SSL, shell mode)
4. Optionally **Test connection**, then save.

Hosts are stored in `data/hosts.json` under the server working directory. Do not commit real passwords.

## Sessions

- Open a host from the **+** menu to create a tab.
- Each tab is an independent session with its own xterm instance.
- Closing a tab ends the remote session (watch for unsaved work).

## Fleet broadcast

OS-aware multi-host broadcast and executive reports: see **Fleet (cluster broadcast)** in the help nav.

## Terminal tips

- Keyboard input goes to the remote shell, like a normal SSH client.
- Use the status bar for font size; the target OS selector affects some NL adaptations.

## Data directory

The server reads `data/` from the **working directory at startup**:

- Dev checkout: usually start under `Assistant/`.
- Clean `pip install` trials: prepare `data/` in your own work folder (e.g. `C:\ChibyWork`) before starting.
