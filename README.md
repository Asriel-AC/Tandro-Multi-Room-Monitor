# Tandro Multi-Room Monitor

Ein visuelles Tool zur gleichzeitigen Überwachung und Verwaltung mehrerer Chat-Räume. Es bietet eine übersichtliche Benutzeroberfläche, automatische Raumwechsel, einen AFK-Modus und lokale Chat-Logs.

[![Screenshot der Benutzeroberfläche](https://i.ibb.co/gbkcGWjN/Screenshot-2026-09-01-180810.png)](https://ibb.co/nqxKT1g9)

## Funktionen

* **Multi-Room Monitoring:** Überwache Chats aus verschiedenen Räumen gleichzeitig in einem einzigen Fenster.
* **Live-Nutzerliste:** Sieh auf einen Blick, welche Nutzer online sind und in welchem Raum sie sich befinden.
* **Auto-Switching:** Das Tool kann automatisch in regelmäßigen Abständen zwischen überwachten Räumen hin- und herwechseln.
* **AFK-Modus:** Automatische Antworten, wenn du erwähnt wirst, während du abwesend bist.
* **Chat-Logging:** Speichert Chatverläufe lokal als `.json` und `.log` Dateien zur späteren Ansicht.
* **Automatischer Login:** Meldet sich selbstständig über deinen Browser im Hintergrund an (benötigt Google Chrome).

---

## Installation & Start

Du hast zwei Möglichkeiten, das Programm zu nutzen. Wenn du dich nicht mit Programmierung auskennst, wähle **Option 1**.

### Option 1: Die einfache Variante (.exe)

Dies ist der empfohlene Weg für die meisten Nutzer. Du musst kein Python installieren.

1. Gehe oben auf dieser GitHub-Seite auf **Releases** (rechts an der Seite).
2. Lade die aktuellste `.exe`-Datei herunter.
3. Speichere die Datei in einem eigenen Ordner auf deinem Computer (das Programm wird dort Konfigurationsdateien und Chat-Logs erstellen).
4. Führe die `.exe`-Datei per Doppelklick aus.

### Option 2: Für Entwickler (Python-Skript)

Wenn du den Quellcode selbst ausführen oder verändern möchtest, folge diesen Schritten:

1. Stelle sicher, dass [Python 3](https://www.python.org/downloads/) auf deinem System installiert ist.
2. Lade den Code herunter oder klone das Repository.
3. Öffne ein Terminal (Eingabeaufforderung) in dem Ordner, in dem die Dateien liegen.
4. Installiere die benötigten Bibliotheken mit folgendem Befehl:
   
   ```bash
   pip install "python-socketio[client]" selenium
   ```

5. Starte das Skript:
   
   ```bash
   python multiroom_gui.py
   ```

---

## Einrichtung und erster Start

Beim ersten Start des Programms (egal ob `.exe` oder Skript) wird automatisch eine Datei namens `multi_room_config.json` erstellt. 

Damit sich das Tool erfolgreich verbinden kann, musst du deine Zugangsdaten hinterlegen. 

**So richtest du es ein:**

1. Öffne die neu erstellte Datei `multi_room_config.json` mit einem einfachen Texteditor (z.B. dem Windows Editor).
2. Trage deinen Benutzernamen und dein Passwort in die entsprechenden Felder ein:
   
   ```json
   "username": "DEIN_NAME",
   "password": "DEIN_PASSWORT",
   ```

3. Speichere die Datei und starte das Tool neu.

*Hinweis zum Login:* Das Programm nutzt einen unsichtbaren Google Chrome Browser, um sich automatisch für dich einzuloggen und das benötigte Token zu holen. **Stelle daher sicher, dass Google Chrome auf deinem PC installiert ist.**

---

## Bedienung der Oberfläche

Das Fenster ist in drei Hauptbereiche unterteilt:

1. **Linke Spalte (Steuerung & Einstellungen):**
   * Hier siehst du deinen Verbindungsstatus.
   * Du kannst den Chat-Logger, Auto-Switching und den AFK-Modus ein- und ausschalten.
   * **Raumliste & Steuerung:** Eine Liste aller bekannten Räume. Du kannst Räume markieren, um sie zu überwachen, oder mit einem Doppelklick direkt beitreten. Du kannst auch manuell X/Y-Koordinaten eintragen und speichern.

2. **Mittlere Spalte (Live-Chat):**
   * Zeigt alle eingehenden Nachrichten der Räume, die du aktuell überwachst.
   * Unten gibt es ein Textfeld, um selbst Nachrichten in den *aktuellen* Raum zu senden.

3. **Rechte Spalte (Online-Nutzer):**
   * Eine alphabetisch sortierte Liste aller bekannten Nutzer, die online sind, inklusive des Raums, in dem sie sich gerade aufhalten.

---

## Bekannte Probleme / Fehlerbehebung

* **Das Tool bleibt auf "Offline (Connecting...)" stehen:** 
  Prüfe, ob dein Benutzername und Passwort in der `multi_room_config.json` korrekt sind. Stelle außerdem sicher, dass Google Chrome installiert ist.
* **Antiviren-Warnung bei der .exe-Datei:** 
  Da die `.exe` von uns selbst kompiliert wurde und keine teure digitale Signatur besitzt, schlagen manche Antivirenprogramme oder Windows SmartScreen fälschlicherweise Alarm (False Positive). Du kannst das Programm in diesem Fall manuell zulassen oder alternativ Option 2 (das Python-Skript) verwenden.
