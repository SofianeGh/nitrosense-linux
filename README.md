# NitroSense Linux

Équivalent de NitroSense (Acer) pour Linux, écrit pour l'**Acer Nitro AN515-58**.
Projet personnel non officiel, sans lien avec Acer.

Les commandes envoyées au BIOS sont celles de NitroSense 3.1, relevées sur ce PC sous Windows
(voir [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md)).

## Fonctions

| Fonction | Comme NitroSense | Passe par |
|---|---|---|
| Modes Silencieux / Par défaut / Performance | oui (Performance seulement sur secteur, ≥ 40 % de batterie) | `platform_profile` du noyau, sinon WMI |
| Mode Turbo (proposé par KDE, pas par NitroSense) | conservé s'il est choisi ailleurs ; `nitrosense-cli profile turbo` | `platform_profile` |
| Ventilateurs Auto / Max / Perso (CPU et GPU, case Auto par ventilateur) | oui (désactivé en Silencieux) | WMI via `acpi_call`, sinon pwm du noyau (≥ 6.18) |
| Régime des ventilateurs, températures, charge, fréquences | oui, avec courbes et min / max | hwmon (`acer`, `coretemp`), `nvidia-smi`, sinon WMI |
| Clavier RGB 4 zones : statique, respiration, onde, zoom, déplacement, néon | oui | WMI via `acpi_call` |
| Extinction du clavier après 30 s | oui | WMI via `acpi_call` |
| Limite de charge à 80 % (Acer Care Center) | en plus | `charge_control_end_threshold`, sinon WMI |
| Mode GPU (MUX) | affiché seulement | WMI (lecture) |
| Sécurité thermique : en Perso, retour en Auto si CPU ≥ 92 °C ou GPU ≥ 87 °C | en plus | |
| Réglages réappliqués au démarrage et après la veille | oui | service systemd |

Non repris : son DTS, Planet9, overclocking GPU, changement du MUX (à faire depuis Windows).

## Architecture

- `nitrosensed` : service root (systemd). Seul à toucher le matériel. Socket `/run/nitrosense/nitrosense.sock`,
  accès limité aux utilisateurs de `allowed_users` (`/etc/nitrosense/config.json`) ou, si la liste est vide,
  aux membres de `sudo` / `wheel` / `admin`.
- `nitrosense` : interface PyQt6 (look NitroSense). `nitrosense-cli` : ligne de commande.
- Le noyau n'a pas d'interface générique pour appeler une méthode WMI : le service utilise le module
  **acpi_call** (`/proc/acpi/call`) pour exécuter la méthode ACPI derrière chaque GUID WMI Acer.

## Installation

1. Dépendances :
   - Debian / Ubuntu / Parrot : `sudo apt install python3 python3-pyqt6 acpi-call-dkms`
     (testé sur Parrot 7.4, noyau 7.1 : le module se compile et se charge sans souci, Secure Boot désactivé)
   - Fedora : `sudo dnf install python3 python3-pyqt6 dkms kernel-devel`, puis acpi_call depuis
     https://github.com/nix-community/acpi_call (DKMS)
   - Arch : `sudo pacman -S python python-pyqt6 acpi_call-dkms` (ou `acpi_call` pour le noyau `linux`)
   - Avec Secure Boot actif, le module DKMS doit être signé (clé MOK à enrôler au redémarrage).
2. Vérifier que le noyau expose les modes : `cat /sys/firmware/acpi/platform_profile_choices`.
   Si le fichier est absent : `sudo modprobe -r acer_wmi && sudo modprobe acer_wmi predator_v4=1`,
   et si ça marche, installer avec `--predator-v4` pour le rendre permanent.
3. Installer : `sudo bash install.sh` (ou `sudo bash install.sh --predator-v4`).
4. Lancer « NitroSense Linux » depuis le menu, ou `nitrosense`.

Désinstaller : `sudo bash uninstall.sh` (ajouter `--purge` pour effacer aussi la configuration).

## Ligne de commande

    nitrosense-cli status
    nitrosense-cli selftest                       # lecture seule de toutes les interfaces
    nitrosense-cli profile quiet|default|performance|turbo
    nitrosense-cli fan auto|max
    nitrosense-cli fan custom --cpu 60 --gpu 70 [--cpu-auto] [--gpu-auto]
    nitrosense-cli kb static --zones FF0000,00FF00,0000FF,FFFFFF --brightness 75
    nitrosense-cli kb effect wave --speed 5 --direction left --color 00FFFF
    nitrosense-cli kb off
    nitrosense-cli backlight-timeout on|off
    nitrosense-cli battery-limit on|off
    sudo nitrosense-cli raw gaming 5 01020000     # appel WMI brut (root) : régime du ventilateur CPU

## Tester sans le PC

    python3 -m unittest discover -s tests -t . -v   # tests (ceux de l'interface demandent PyQt6)
    python3 -m nitrosense.gui --demo                 # interface sur un PC simulé
    python3 -m nitrosense.cli --demo status

## Dépannage

- **« Service nitrosensed injoignable »** : `systemctl status nitrosensed`, `journalctl -u nitrosensed -b`.
- **Ventilateurs Perso / clavier indisponibles** : `acpi_call` n'est pas chargé (`ls /proc/acpi/call`).
- **Mode Performance grisé** : brancher le secteur (règle de NitroSense), ou mettre
  `"performance_requires_ac": false` dans `/etc/nitrosense/config.json`.
- **Fenêtre impossible à déplacer (Wayland)** : lancer `nitrosense --native-frame`.
- **Widget batterie de KDE / GNOME (power-profiles-daemon)** : il change aussi le mode. Le service suit
  ces changements (« Performance » de KDE = Turbo, affiché sous les boutons de mode) et remplace
  « Économie d'énergie » (Éco, non géré par ce BIOS) par Silencieux (`"eco_to_quiet": false` pour l'empêcher).
  Dans les 20 s après le démarrage ou la veille, c'est le mode enregistré dans NitroSense qui gagne.
- **Autres conflits** : tuned, TLP ou auto-cpufreq peuvent aussi changer `platform_profile`.
- **Diagnostic complet** (lecture seule) : `sudo bash tools/linux_probe.sh`.

## Sécurité

- Aucun accès direct aux registres de l'EC, aucune méthode BIOS (mots de passe, démarrage, overclocking).
- Écritures bloquées si le modèle n'est pas un AN515-58 (`"allow_unknown_model": true` pour forcer).
- Ventilateurs remis en Auto quand le service s'arrête, et sécurité thermique en mode Perso.
