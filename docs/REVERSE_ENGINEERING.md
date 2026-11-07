# Ce que fait NitroSense 3.1 sur cet AN515-58

Relevé sous Windows 11 sur la machine elle-même, le 2026-11-06, en lecture seule :
classes WMI (`Get-CimClass root\WMI`), registre et fichiers de NitroSense,
arbre d'interface (UI Automation) et désassemblage IL de `NitroSense.exe` (.NET).

## Machine

| | |
|---|---|
| Modèle | Acer Nitro AN515-58 (Nitro 5), carte « Jimny_ADH » |
| BIOS | Insyde V2.21 (2026-06-09), interface WMI Acer v2.86 |
| CPU / GPU | i5-12450H ; RTX 4060 Laptop (PCI 10DE:28A0, sous-système 159C:1025) + Intel UHD |
| Écran / RAM | 1920×1080 144 Hz ; 16 Go |
| NitroSense | V3.1 3.1.3052.0 (UWP + `NitroSense.exe` WPF .NET) ; service natif `PSSvc.exe`, pipe nommé `predatorsense_service_namedpipe` |
| Matériel déclaré (`HW_Support.ini`) | clavier RGB **4 zones** (`PerKey=0`) ; ventilateurs CPU=1, GPU=1, Système=0 ; LCD overdrive non supporté ; commutateur MUX supporté |
| Disque | GPT : EFI, MSR, C: NTFS 378 Go (non chiffré, démarrage rapide désactivé), Linux 97,7 Go, Recovery |

## Classes WMI (root\WMI)

| Classe | GUID | Rôle |
|---|---|---|
| AcerGamingFunction | `7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56` | ventilateurs, modes, capteurs, clavier |
| APGeAction | `61EF69EA-865C-4BC3-A502-A0DEBA0CB531` | fonctions « génériques » (délai du rétroéclairage) |
| BatteryControl | `79772EC5-04B1-4BFD-843C-61E7F77B6CC9` | limite de charge 80 % (Acer Care Center) |
| AcerGenericMethod | `1EA0BC57-521E-461B-A7A9-5D36F141A82A` | non utilisée |
| AcerBiosConfigurationTool, BIOSSetting, UtilityFunction, DataTrans | — | **réglages BIOS : ne jamais appeler** |

Méthodes d'AcerGamingFunction (WmiMethodId) : 1 SetGamingProfile, 2 SetGamingLED,
3 GetGamingProfile, 4 GetGamingLED, 5 GetGamingSysInfo, 6 SetGamingRgbKb, 7 GetGamingRgbKb,
8/9 Set/GetGamingProfileSetting, 10/11 Set/GetGamingLEDBehavior, 12/13 Set/GetGamingLEDColor,
14/15 Set/GetGamingFanBehavior, 16/17 Set/GetGamingFanSpeed, 18/19 Set/GetGamingFanTable,
20/21 Set/GetGamingKBBacklight (entrée u8[16]), 22/23 Set/GetGamingMiscSetting,
24/25 Set/GetCPUOverclockingProfile (**ne pas toucher**).

Types : les « Get » prennent un u32 (4 octets) et rendent un u64 ; les « Set » prennent un
u64 (8 octets) et rendent un u32. L'octet de poids faible du résultat est un statut (0 = OK).

Sous Linux, la méthode ACPI derrière un GUID est `<chemin du PNP0C14>.WM<object_id>`
(voir `/sys/bus/wmi/devices/<GUID>/object_id` et `firmware_node/path` du périphérique parent),
appelée avec (instance 0, id de méthode, tampon d'entrée). `tools/linux_probe.sh` l'affiche.

## Encodages, fonction par fonction

### Ventilateurs (`CommonFunction.set_all_fan_mode`, `set_all_custom_fan_state`, `set_single_custom_fan_speed`)

- **SetGamingFanBehavior (14)** : bits 0-15 = groupes (CPU `0x0001`, GPU `0x0008`) ;
  mode CPU bits 16-17, mode GPU bits 22-23 ; 1 = auto, 2 = max, 3 = perso.
  - Auto `0x00410009`, Max `0x00820009`, Perso `0x00C30009`, Perso avec CPU en auto `0x00C10009`.
- **SetGamingFanSpeed (16)** : id (CPU `0x01`, GPU `0x04`) | pourcentage << 8. CPU 60 % = `0x3C01`.
- Séquence « Perso » : un SetGamingFanBehavior (les deux groupes), puis la vitesse CPU, puis la vitesse GPU.
  Déplacer un curseur n'envoie que SetGamingFanSpeed.
- Règle de l'interface : contrôle des ventilateurs désactivé en mode Silencieux.

### Capteurs (`get_wmi_system_health_info`)

- **GetGamingSysInfo (5)** : entrée `0x01 | id << 8` ; sortie `statut | valeur << 8` (16 bits).
- id : 1 CPU °C, 2 ventilateur CPU tr/min, 3 temp. système, 4 ventilateur système,
  6 ventilateur GPU, 7 temp. système 2, 10 GPU °C, 11 GPU2 °C.
- Entrée `0x0002` : état batterie ; bits 40-47 == 1 → « battery boost ».

### Modes de performance (`OPMode_Type`, `MonitorAndOCClass.set_operation_mode`)

- Silencieux = 0, Par défaut = 1, Performance = 4 (« Extreme » dans le code), Turbo = 5 (absent
  de l'interface sur ce modèle). L'interface envoie la valeur au service (commande pipe 30),
  qui la passe au BIOS ; même encodage que le pilote Linux acer-wmi :
  SetGamingMiscSetting `0x0B | valeur << 8`, lecture GetGamingMiscSetting(`0x0B`),
  modes supportés GetGamingMiscSetting(`0x0A`) = bitmap (bit n = mode n).
- Correspondance `platform_profile` d'acer-wmi (mode predator_v4) : quiet ↔ 0, balanced ↔ 1,
  **balanced-performance ↔ 4 (Performance NitroSense)**, performance ↔ 5 (Turbo), low-power ↔ 6.
- Règle NitroSense : changement de mode seulement sur secteur avec au moins 40 % de batterie.
- En mode Performance, NitroSense applique aussi un OC GPU de +100 MHz (cœur) / +200 MHz
  (mémoire) via NVAPI (`NitroSense.ini`) : non reproduit.

### Clavier RGB 4 zones (`Popup_Lighting`, `LightingDynamicUI.Set_Dynamic_Keyboard`)

- **Statique** :
  SetGamingKBBacklight (20) = `[0, 0, luminosité, 0…]` ;
  couleur d'une zone SetGamingRgbKb (6) = `zone (1, 2, 4, 8) | R << 8 | G << 16 | B << 24` ;
  zones allumées SetGamingLEDBehavior (10) = `0x08 | (zone n allumée → bit 40+n)`.
- **Dynamique** : SetGamingKBBacklight (20), octets
  `[effet, vitesse, luminosité, 0x08 si onde, direction, R, G, B, 0 × 8]` ;
  effets 1 respiration, 2 néon (sans couleur), 3 onde, 4 déplacement, 5 zoom ;
  direction 1 = droite, 2 = gauche (onde et déplacement seulement).
- Luminosité : curseur à 5 crans, `(niveau − 1) × 25` → 0, 25, 50, 75, 100.
- NitroSense passe un u64 au service, qui le convertit en u8[16] : les octets 8-15 sont supposés nuls.

### Extinction du clavier après 30 s (`Backlightoff_Checkbox_*`, APGeAction)

- Lecture GetFunction (2) : `0x01 | hotkey << 8 | 0x0008 << 16` = `0x88401` →
  statut (bits 0-7), luminosité (bits 32-39), délai (bits 40-47, 30 = activé).
- Écriture SetFunction (1) : `0x02 | hotkey << 8 | 0x0008 << 16 | luminosité << 32 | (30 ou 0) << 40`.
- hotkey = `BK_Hotkey_Number` du registre = 132 (`0x84`).

### Commutateur MUX « GPU distinct seulement » (lecture seule ici)

- Lecture GetGamingMiscSetting(`0x02`) → 1 hybride, 2 GPU dédié seul ; support : GetGamingMiscSetting(`0x09`) == 3.
- Écriture SetGamingMiscSetting(`0x02 | (2 ou 1) << 8`) puis redémarrage. NitroSense fait une
  sonde avant (écrit, vérifie la sortie DisplayPort USB-C, remet) : **non reproduit volontairement**
  (risque d'écran noir sous Linux si le pilote NVIDIA ne démarre pas).

### Limite de charge 80 % (Acer Care Center, BatteryControl)

- GetBatteryHealthControlStatus (20) : entrée `[1, 1, 0, 0]` → sortie
  `[liste, retour0, retour1, état santé, état calibration, 0, 0, 0]`.
- SetBatteryHealthControl (21) : entrée `[1, masque (1 santé, 2 calibration), 0/1, 0 × 5]` → u16 retour.
- Même format que le module hors noyau acer-wmi-battery. Le service vérifie en relisant.

## État observé sous Windows (référence pour comparer)

- Mode Silencieux, ventilateurs Auto (verrouillés), environ 2450-2600 tr/min au repos ;
  en charge avec le mode Performance : environ 5880 (CPU) / 6250 (GPU) tr/min.
- Clavier : dynamique « Onde » vers la droite, blanc. Touche Windows activée, °C,
  extinction du clavier après 30 s activée, MUX : hybride.

## Interface de NitroSense 3.1 (inventaire)

- En-tête : Planet9 (pub), éclairage clavier, mode son (DTS), paramètres, réduire, fermer.
- « Ctrl ventilo » : Auto / Max / Perso ; en Perso, un curseur + une case « Auto » par ventilateur ;
  régimes CPU et GPU au centre de deux ventilateurs ; message rouge en mode Silencieux.
- « Mode » : fréquences CPU / GPU en MHz ; Silencieux / Par défaut / Performance.
- « Surveillance » : courbes température (rouge) et charge (orange) CPU et GPU, min / max,
  « GPU distinct inactif. » quand le GPU dédié dort.
- Éclairage : Statique / Dynamique, luminosité, effets, vitesse, direction, 9 couleurs de base,
  9 couleurs récentes, « Plus de couleur… », 4 sections avec case et couleur.
- Paramètres : touches rémanentes (Windows), touche Windows/menu, unités °C/°F,
  extinction du clavier après 30 s, « GPU distinct seulement ».

## Vérifié sous Linux (2026-11-07, Parrot 7.4, noyau 7.1.13, acpi_call 1.2.2)

- Méthodes ACPI : `\_SB_.PC00.WMID.WMBH` (AcerGamingFunction), `.WMAA` (APGeAction), `.WMBE` (BatteryControl).
- Le pilote acer-wmi du noyau 7.1 reconnaît l'AN515-58 (quirk `predator_v4` + `pwm`, sans option) :
  `platform_profile` et hwmon `acer` (fan1/fan2, temp1/3, **pwm1/pwm2 + pwm*_enable** :
  0 = max, 1 = perso, 2 = auto ; pwm 0-255 → 0-100 %). Il envoie les mêmes codes WMI que NitroSense.
- Lectures (`nitrosense-cli selftest`) : capteurs identiques à hwmon ; mode courant ; MUX = 1 (hybride),
  support MUX = 3 ; APGe `0x00001E0000000000` (délai 30 s activé, champ luminosité = 0) ;
  batterie `03 00 00 00 00 00 00 00` (santé + calibration disponibles, toutes deux désactivées).
- **Bitmap des modes `0x0A` = `0x33`** (et non `0x13`) : Silencieux, Par défaut, Performance, **Turbo**.
  Le BIOS accepte aussi Éco (6) sans l'annoncer. acer-wmi propose les 5 modes quel que soit le bitmap,
  et power-profiles-daemon (widget batterie de KDE) choisit `low-power` (Éco) pour « Économie d'énergie »
  et `performance` (Turbo) pour « Performance ».
- Ventilateurs (WMI direct, codes NitroSense) : Max → ~7300 (CPU) / ~7500 (GPU) tr/min ;
  Perso 30 % → ~3750 ; 70 % → ~5800 ; « GPU auto » fonctionne ; retour Auto en ~4 s.
  Au repos et à froid, les ventilateurs s'arrêtent (0 tr/min) en Auto.
  GetGamingFanBehavior / GetGamingFanSpeed : formats confirmés (relus par acer-wmi).
- Clavier statique (couleur unique, 4 zones de gauche à droite, zone éteinte, luminosité) : **confirmé à l'œil**.
  Effets dynamiques : acceptés par le BIOS (statut 0) ; rendu à confirmer.

## Points restant à vérifier

1. Rendu des effets dynamiques (octets 8-15 du tampon de SetGamingKBBacklight supposés nuls).
2. Extinction réelle du clavier après 30 s, et effet du champ luminosité = 0 réécrit par SetFunction.
3. Limite de charge 80 % : écriture et relecture (indices de sortie de BatteryControl).
