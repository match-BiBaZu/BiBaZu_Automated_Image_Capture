# Projektübergabe — festes Gestell und Zweimotoren-Förderband

Aktueller Aufbau, Stand 10. Oktober 2026: Die Rutsche sitzt fest auf einem Gestell.
Die GUI stellt ihre Neigung nicht ein und verwendet keine Roboterverbindung.
Der vollständige aktuelle Bedienablauf, Installation, Kamera-Bildrate, OBB-Pipeline,
Training und Bereinigung stehen in [README.md](README.md).

## Aufbau und Schnittstelle

| Gerät | Anschluss |
| --- | --- |
| Baumer-Kamera | 169.254.117.70, GigE/GenTL, Baumer-CTI |
| Förderband | SPS 192.168.10.23, AMS 10.145.4.14.1.1, Port 851 |
| Motor 1 | Device 3 / Term 7, EL7201-0010, CSV 9 |
| Motor 2 | Device 4 / Term 10, EL7201-0010, CSV 9 |
| Zwei RGB660 Pro II | getrennte BLE-Adressen |

Beide Motoren folgen einem gemeinsamen Geschwindigkeitsprofil. Die Rutsche hat
eine feste Kameraansicht; ihre tatsächliche Neigung wurde nicht angegeben.
Die bekannten Gerätestatus-/Kalibrierungsnamen `MAIN.Stepper*` sind virtuelle
Kompatibilitätssignale mit tatsächlicher Servo-Encoder-Rückmeldung.

Die aktuelle SPS-Quelle ist das bestehende `TwinCAT Projekt3 - Kopie` im
Geschwisterrepository `BiBaZu_Big_Boi/CSVSaver`. Der kleine Aufnahme-Umbau dort
entfernt die alte 500-Schritte/s-Begrenzung endlicher Fahrten, hebt deren
Distanzgrenze auf 250000 Schritte an und ergänzt
`MAIN.ConveyorCaptureInterfaceVersion = 1`. Vor Bandbetrieb muss dieser
Quellstand kompiliert und auf die SPS geladen werden. Die GUI überträgt kein
SPS-Programm und aktiviert keine Hardwarekonfiguration.

## Softwareänderungen

- `hardware/robot.py`, Roboterstatus/-modi, Roboter-IP, Pose-/Winkelauswahl,
  Roboter-Handshake und zugehörige Programme/Tests wurden entfernt.
- `ur-rtde` wird nicht mehr benötigt.
- Stationen laufen Band → Belichtung → Licht; synchronisierte Serien laufen
  Belichtung → vollständige Band-Rundfahrt.
- Kamera, beide Panels, Inferenz, OBB-Annotation, Kuratierung, Training,
  Speicherbereinigung und Fortsetzen bleiben Teil derselben GUI.
- Förderbandbefehle bereiten stets beide Motoren gemeinsam im Stillstand vor.
  Stop kann die Vorbereitung abbrechen. Geschwindigkeit ist maximal 200 mm/s;
  die Aufnahme-Beschleunigung ist einstellbar, standardmäßig 50 rpm/s.
- Synchronisierte Planung berücksichtigt Beschleunigung und Abbremsen. Geplante
  Zeit/Position und gemessene Encoderposition bleiben getrennte Metadaten.
- Endliche Geschwindigkeitsfahrten haben keine Positionskorrektur. Restabweichungen
  bis 1 mm (mindestens bisherige Vollschritt-Toleranz) sind erlaubt; größere
  Abweichungen brechen ab. Die GUI sendet keine automatische Nachstellfahrt.

## Einstellungen und Datenkompatibilität

Die vorhandenen `QSettings` unter
`LeibnizUniversitaetHannover/AutomatedImageCapture` behalten ihre Kamera-, Licht-,
Belichtungs-, Band- und Ausgabeeinstellungen. Alte Roboterwerte werden ignoriert.
Neue Dateien verwenden `fixed` im Namen. PNG-/YAML-Paare und
`capture_session.json` bleiben erhalten; Metadaten und Checkpoints sind Version 3.
Die Montage steht als `chute.mounting: fixed` und
`chute.inclination_adjustable: false` in der Bild-YAML.

Frühere Bildnamen und Label-Berichte bleiben offline lesbar. Dafür bleiben die
alten Modus-/Ansichtsfelder als Datensatz-Kompatibilitätsformat erhalten; sie
steuern keine Hardware. Alte unterbrochene UR-Aufnahmen werden nicht fortgesetzt,
da ihre Orientierungsschleifen nicht zum Gestell passen.

Neue feste Ansichten verwenden für Train/Val/Test vollständige Lichtpaare als
Gruppen. Mindestens drei Lichtpaare sind nötig. Alle Klassen, Leerbilder,
Belichtungen und Bandframes eines Lichtpaars bleiben im gleichen Split;
benachbarte Frames werden nicht zufällig verteilt. Das prüft Variation der
Beleuchtung am festen Aufbau; unabhängige Bauteile/Serien müssen separat
aufgenommen werden, wenn diese Generalisierung gemessen werden soll.

## Inbetriebnahme nach dem Umbau

1. `uv sync --extra dev`, Baumer-CTI, ADS-Runtime und Bluetooth-Adapter bereitstellen.
2. Beidseitige AMS-Route und Kamera-/SPS-Netzwerke einrichten.
3. Angepasste vorhandene SPS-Quelle kompilieren und laden; beide Antriebe und
   Encoder prüfen. `PressureControlGUI` während Capture nicht parallel betreiben.
4. Kamera und Lichter verbinden, Livebild und Rohabrufrate prüfen.
5. Förderband verbinden und eine kurze Testfahrt durchführen; Vorwärtsrichtung
   bestimmen und tatsächliche Encoderbewegung prüfen.
6. Bauteil hinten platzieren, Nullpunkt übernehmen und eine kleine Licht-/Bandserie
   aufnehmen. Rückfahrt, Bild-/YAML-Paare und Positionen kontrollieren.
7. Unterbrechen/Fortsetzen, Leerbildserie, OBB-Review und Datensatzaufbereitung prüfen.

## Verifikation

Automatisierte Tests verwenden simulierte Geräte. Reale Kamera-/BLE-Tests sind
weiterhin opt-in über `RUN_HARDWARE_TESTS=1`. Ein Softwaretest oder Offline-Build
belegt keine erfolgreiche beladene Tandemfahrt am neu aufgebauten Förderband.
Die dokumentierte frühere Hardwarehistorie steht in
`BiBaZu_Big_Boi/CSVSaver/TANDEM_CONVEYOR.md`.

## Archiv: frühere Datensätze und Modellläufe

Die folgenden Abschnitte dokumentieren frühere Serien mit verstellbarer Rutsche.
Winkelwerte und Aufnahmebedingungen gelten für diese archivierten Datensätze.

## 16. Ql1i: Quelldaten, OBBs und Kuratierung

Die drei Rohserien liegen außerhalb des Repositories auf Laufwerk `D:`:

```text
Pose 1: D:\pictures\Ql1i\capture_20260808_165705
Pose 2: D:\pictures\Ql1i\capture_20260808_170121
Leer:   D:\pictures\Ql1i\capture_20260808_170321
```

Jede Serie enthält 400 Mono8-Bilder mit 1920 × 1464 Pixeln und 5000 µs Belichtung. Verwendet
wurden die vier UR-Winkel `155`, `180`, `205` und `210`. Der ursprüngliche, teilweise
unvollständige Ordner `D:\pictures\Ql1i\OBB` wurde bewusst nicht überschrieben.

Erhaltene Vergleichsläufe:

```text
D:\pictures\Ql1i\OBB_codex_20260809_v1
D:\pictures\Ql1i\OBB_codex_20260809_v1_review_snapshots
D:\pictures\Ql1i\OBB_codex_20260809_v2_mindiff20
D:\pictures\Ql1i\OBB_codex_20260809_v2_mindiff20_review_snapshots
```

V1 mit Mindestdifferenz 80 war stabiler als V2 mit Mindestdifferenz 20. V1 erzeugte pro
Klasse/Winkel 20/12/17/10 beziehungsweise 23/19/15/11 sichere Anker, 674 Review-Hinweise und
333 positionsinterpolierte OBBs. Die empfindlichere V2-Konfiguration verschlechterte das
Ergebnis auf 702 Review-Hinweise und weniger stabile Pose-1-Anker. Deshalb verwendet der
finale Lauf wieder Mindestdifferenz 80:

```text
Finale OBBs:       D:\pictures\Ql1i\OBB_final_20260809
Review-Snapshots:  D:\pictures\Ql1i\OBB_final_20260809_review_snapshots
```

Finaler OBB-Stand:

- 766 positive Bilder und 387 passende Leerbilder exportiert,
- 34 unbrauchbar dunkle positive Bilder automatisch ausgeschlossen,
- 13 damit verknüpfte Leerbilder ebenfalls nicht als Negativbeispiele übernommen,
- 800 Positivbilder positionsgeführt, davon 333 aus der Bahn interpoliert,
- 799 Positionskorrekturen und 674 Review-Hinweise vor den Ausschlüssen,
- zwei Klassen und vier Winkel,
- keine Quelldateien verändert.

Die Ursache der problematischen Bilder ist nicht nur eine schwache Segmentierung: Trotz
gleicher nomineller Lichtbefehle unterscheiden sich Objekt- und Leeraufnahme optisch stark.
Beispielsweise liegt der Vordergrundmittelwert einer Pose-1-Aufnahme bei 40/40 ungefähr bei
39, der des passenden Leerbildes ungefähr bei 163. Zusätzlich wirft das Bauteil einen langen
Schlagschatten, der rohe OBBs nach links ziehen kann. Die BLE-Bestätigung bestätigt nur das
Kommando, nicht die tatsächlich erreichte optische Beleuchtung.

Der finale kuratierte Datensatz liegt hier:

```text
D:\pictures\Ql1i\YOLO_final_20260809\dataset_20260809_200522
```

Er enthält 1.153 Bilder: 380 Pose 1, 386 Pose 2 und 387 Leerbilder. Die winkelreine Aufteilung
ist:

| Split | Winkel | Pose 1 | Pose 2 | Leer | Gesamt |
| --- | --- | ---: | ---: | ---: | ---: |
| Train | 15,5° + 18,0° | 189 | 191 | 191 | 571 |
| Validation | 20,5° | 96 | 95 | 96 | 287 |
| Test | 21,0° | 95 | 100 | 100 | 295 |

Verlustfreie Resize-Caches wurden für 640 und 960 Pixel erhalten:

```text
D:\pictures\Ql1i\YOLO_final_20260809\dataset_20260809_200522_imgsz640
D:\pictures\Ql1i\YOLO_final_20260809\dataset_20260809_200522_imgsz960
```

## 17. Df1a: erhaltener Referenzlauf

Die ursprünglichen OBBs liegen unter:

```text
C:\Users\Administrator\Pictures\Df1a\OBB
```

Sie enthalten 2.420 Bilder: vier Klassen mit je 484 positiven Bildern sowie 484 Leerbilder.
Der kuratierte Datensatz wurde als
`C:\Users\Administrator\Pictures\Df1a\YOLO\dataset_20260809_184640` erhalten und besitzt
1.210 Train-, 605 Validation- und 605 Testbilder. Der 640-Pixel-Cache liegt daneben als
`dataset_20260809_184640_imgsz640`.

Der finale Trainingslauf liegt unter:

```text
C:\Users\Administrator\Pictures\Df1a\YOLO\runs\Df1a_yolo26n_obb_20260809
```

Er war auf 50 Epochen mit Patience 12 angesetzt, stoppte nach Epoche 16 und wählte Epoche 4.
Das stabile, gegen den Run-Checkpoint per SHA geprüfte Modell ist:

```text
C:\Users\Administrator\Pictures\Df1a\YOLO\Df1a_best.pt
```

Unabhängiger Test: Precision 0,93386, Recall 0,94725, mAP50 0,98628 und mAP50–95 0,89037.
Auf allen 121 Leerbildern trat kein Fehlalarm auf. Eine zusätzliche praktische Stichprobe mit
je einem Bild pro Klasse plus Leerbild wurde vollständig richtig klassifiziert.

## 18. Kk1a: finaler OBB- und YOLO-Lauf

Die fünf Rohserien liegen unter `C:\Users\Administrator\Pictures\AutomatedImageCapture`.
Chronologisch wurden sie wie folgt zugeordnet:

```text
Pose 1: capture_20260810_213525
Pose 2: capture_20260810_214724
Leer:   capture_20260810_215123
Pose 3: capture_20260810_215358
Pose 4: capture_20260810_220005
```

Jede Serie enthält 500 vollständige PNG/YAML-Paare: je 100 Bilder bei den Winkeln 15,5°,
17,0°, 18,5°, 20,0° und 21,0°. Der finale OBB-Lauf und seine separat erhaltenen
Review-Snapshots liegen hier:

```text
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_OBB_20260810
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_OBB_20260810_review_snapshots
```

Er enthält 2.000 positive Bilder, 500 Leerbilder, vier Klassen und fünf Winkel. Es wurden
keine Bilder automatisch ausgeschlossen. Alle 2.000 Positivbilder sind positionsgeführt,
1.980 wurden durch das Bahnmodell korrigiert und nur 16 vollständig interpoliert. Die
schwächste Ankergruppe ist Pose 4 bei 17° mit 30/100 Ankern; auch deren Review-Sheet wurde
visuell als plausibel geprüft.

Der kuratierte Datensatz lautet:

```text
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_YOLO_20260810\dataset_20260810_221045
```

| Split | Winkel | Bilder | Je Pose | Leer |
| --- | --- | ---: | ---: | ---: |
| Train | 15,5° + 17,0° + 20,0° | 1.500 | 300 | 300 |
| Validation | 18,5° | 500 | 100 | 100 |
| Test | 21,0° | 500 | 100 | 100 |

Training: YOLO26n-OBB, 640 Pixel, Batch 16, acht Worker, maximal 75 Epochen und Patience 15.
Early Stopping beendete den Lauf nach 21 Epochen; beste Trainingsepoche war Epoche 6. Der
vollständige Run liegt unter:

```text
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_YOLO_20260810\runs\Kk1a_yolo26n_obb_20260810
```

Empfohlenes Modell und unveränderliche Baseline:

```text
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_YOLO_20260810\Kk1a_best.pt
C:\Users\Administrator\Pictures\AutomatedImageCapture\Kk1a_YOLO_20260810\Kk1a_best_baseline_20260810.pt
```

Beide Kopien und `weights\best.pt` besitzen SHA-256
`033660BA5181DA2DC79E4B1D0BB725DEC098F9A4A451FB8C9290D03DF1829FE5`.

Validation: Precision 0,9545, Recall 0,9493, mAP50 0,9910 und mAP50–95 0,8977.
Unabhängiger 21°-Test: Precision 0,9425, Recall 0,9473, mAP50 0,9882 und mAP50–95 0,8602.
Klassenweises Test-mAP50–95: Pose 1 0,6886, Pose 2 0,8895, Pose 3 0,9321 und Pose 4
0,9308.

Der Rohbild-Konfidenz-Sweep ist als `confidence_sweep.json` erhalten. Für die GUI wird `0,26`
empfohlen: 392/400 Positivbilder korrekt, vier Pose-2-Bilder verpasst, vier Pose-4-Bilder als
Pose 2 erkannt und 0/100 Fehlalarme auf Leerbildern. Bei 0,25 gab es noch einen Leerbild-
Fehlalarm; höhere Schwellen verwerfen zunehmend richtige Pose-2-Bilder.

## 19. Kl1i: finaler OBB- und YOLO-Lauf

Die drei Rohserien liegen unter `C:\Users\Administrator\Pictures\Kl1i`:

```text
Pose 1: capture_20260810_220517
Pose 2: capture_20260810_220814
Leer:   capture_20260810_221324
```

Jede Serie enthält 500 vollständige PNG/YAML-Paare, je 100 bei 15,5°, 17,0°, 18,5°,
20,0° und 21,0°. Finale OBBs und separat erhaltene Review-Snapshots:

```text
C:\Users\Administrator\Pictures\Kl1i\Kl1i_OBB_20260811
C:\Users\Administrator\Pictures\Kl1i\Kl1i_OBB_20260811_review_snapshots
```

Der OBB-Lauf enthält 1.000 positive Bilder und 500 Leerbilder. Es gab keine Ausschlüsse und
keine vollständig interpolierte OBB; alle Positivbilder sind positionsgeführt und 999 Boxen
wurden durch das Bahnmodell stabilisiert. Die schwächsten Gruppen besitzen 35/100 sichere
Einzelanker. Deren Review-Sheets wurden ebenso wie stärkere Referenzgruppen visuell geprüft.

Kuratierter Datensatz:

```text
C:\Users\Administrator\Pictures\Kl1i\Kl1i_YOLO_20260811\dataset_20260811_112136
```

| Split | Winkel | Bilder | Pose 1 | Pose 2 | Leer |
| --- | --- | ---: | ---: | ---: | ---: |
| Train | 15,5° + 17,0° + 20,0° | 900 | 300 | 300 | 300 |
| Validation | 18,5° | 300 | 100 | 100 | 100 |
| Test | 21,0° | 300 | 100 | 100 | 100 |

Das YOLO26n-OBB-Training verwendete 640 Pixel, Batch 16, acht Worker, maximal 75 Epochen und
Patience 15. Es stoppte nach Epoche 42; beste Epoche war 27. Vollständiger Run:

```text
C:\Users\Administrator\Pictures\Kl1i\Kl1i_YOLO_20260811\runs\Kl1i_yolo26n_obb_20260811
```

Empfohlenes Modell und unveränderliche Baseline:

```text
C:\Users\Administrator\Pictures\Kl1i\Kl1i_YOLO_20260811\Kl1i_best.pt
C:\Users\Administrator\Pictures\Kl1i\Kl1i_YOLO_20260811\Kl1i_best_baseline_20260811.pt
```

Beide Kopien und `weights\best.pt` besitzen SHA-256
`642F646E0F7560091F4837833AFC3E9756F1A38A26ABB6D5F5FC18B26CDD0A83`.

Validation: Precision 0,9982, Recall 0,9950, mAP50 0,9950 und mAP50–95 0,9769.
Unabhängiger 21°-Test: Precision 0,9909, Recall 0,9900, mAP50 0,9940 und mAP50–95 0,9603.
Klassenweises Test-mAP50–95: Pose 1 0,9949 und Pose 2 0,9258.

Der Rohbild-Sweep ist als `confidence_sweep.json` erhalten. Für die GUI wird Konfidenz `0,25`
empfohlen. Von 0,05 bis 0,32 bleibt das Ergebnis stabil: 199/200 Positivbilder korrekt, eine
Pose-2-Aufnahme verpasst, keine Klassenverwechslung und 0/100 Leerbild-Fehlalarme. Ab 0,35
wird ein weiteres positives Bild verworfen.

## 20. YOLO-Training und Performance

Das Training läuft als separater Prozess, damit Hardwareanzeige und GUI responsiv bleiben.
Beim Stoppen wird zuerst ein geordneter Abbruch versucht; Checkpoints und Logs bleiben erhalten.

Aktuelle Implementierung und Defaults:

- Modell `yolo26n-obb.pt`, maximal 200 Epochen, Patience 40,
- Bildgröße 640 Pixel,
- Batch 16 und acht DataLoader-Worker,
- Batch und Worker sind im Trainingsdialog einstellbar und werden an den Prozess übergeben,
- Gerät standardmäßig GPU `0`, AMP aktiv,
- Seed 42 und deterministische Ausführung,
- keine Spiegelungen, kein Mosaic, MixUp oder Copy-Paste und keine große Rotation,
- geringe Translation, Skalierung und Helligkeitsvariation,
- einmaliger verlustfreier Resize-Cache, um wiederholtes Dekodieren und Skalieren zu vermeiden.

Batch 16/Workers 8 wurde auf der RTX 2000 Ada mit 8 GB getestet. Bei Df1a sank die gemessene
Epochendauer gegenüber Batch 4/Workers 0 auf ungefähr 17–20 Sekunden; die GPU belegte dabei
nur etwa 2,5–2,9 GB VRAM. Mehr CPU-Auslastung allein ist daher kein sinnvolles Ziel: Training,
Datenladen, GPU-Kernel und Synchronisation begrenzen verschiedene Phasen.

### Ql1i-Modell

Alle finalen Ql1i-Ergebnisse liegen unter:

```text
D:\pictures\Ql1i\YOLO_final_20260809
```

Empfohlenes Modell und unveränderliche Sicherung:

```text
D:\pictures\Ql1i\YOLO_final_20260809\Ql1i_best.pt
D:\pictures\Ql1i\YOLO_final_20260809\Ql1i_best_baseline_20260809.pt
```

Der zugehörige Run `runs\Ql1i_yolo26n_obb_20260809` verwendete 640 Pixel, Batch 16 und acht
Worker. Er war auf 75 Epochen mit Patience 15 angesetzt, stoppte ungefähr nach Epoche 20 und
wählte Epoche 5. Validation: Precision 0,7003, Recall 0,8592, mAP50 0,86646 und mAP50–95
0,54012. Unabhängiger Test: Precision 0,6861, Recall 0,86395, mAP50 0,86166 und mAP50–95
0,60046. Klassenweise erreichte Pose 1 mAP50/mAP50–95 von 0,761/0,472, Pose 2 von
0,962/0,729; die 100 Leerbilder erzeugten keinen Fehlalarm.

Der Konfidenz-Sweep auf allen 295 Testbildern ergab:

| Konfidenz | Pose 1 korrekt | Pose 2 korrekt | Leer korrekt | Klassenverwechslungen |
| ---: | ---: | ---: | ---: | ---: |
| 0,10 | 95/95 | 96/100 | 100/100 | 0 |
| 0,15 | 89/95 | 96/100 | 100/100 | 0 |
| 0,20 | 70/95 | 96/100 | 100/100 | 0 |
| 0,25 | 47/95 | 94/100 | 100/100 | 0 |

Für den aktuellen Ql1i-Liveversuch ist deshalb Konfidenz 0,10 sinnvoll. Der erhaltene
960-Pixel-Vergleich `runs\Ql1i_yolo26n_obb_960_20260809` erzielte zwar auf Validation
mAP50–95 0,77186, fiel auf dem unabhängigen Testwinkel aber auf mAP50 0,6920 und mAP50–95
0,5189 zurück. Insbesondere Pose 1 erreichte dort nur 0,436/0,359. Dieser Lauf hat den
Validation-Winkel überangepasst und wird nicht empfohlen.

### Ql1i-Neuaufnahme vom 12. August 2026

Der verbesserte Rohdatensatz liegt unter:

```text
C:\Users\Administrator\Pictures\Ql1i new
```

Er enthält je 500 Bilder der beiden Werkstücklagen und 500 passende Leerbilder, jeweils mit
den fünf UR-Winkeln 15,5°, 17,0°, 18,5°, 20,0° und 21,0° sowie einem vollständigen
10-×-10-Lichtraster. Der Abgleich mit den historischen Ql1i-Aufnahmen zeigte, dass die zeitlich
erste neue Serie der historischen **Pose 2** und die zweite Serie der **Pose 1** entspricht.
Die finale Klassenreihenfolge lautet deshalb bewusst `0 = Pose 2`, `1 = Pose 1`.

Finale OBBs und Review-Bilder:

```text
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_OBB_20260812_final
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_OBB_20260812_final_review_snapshots
```

Der Lauf erhielt alle 1.000 positiven und 500 leeren Bilder. 996 positive Boxen wurden mit
der gemessenen Förderbandbahn korrigiert, 28 davon mussten zwischen sicheren Ankern
interpoliert werden. 709 Bilder tragen einen konservativen Review-Hinweis; die zehn
Winkel-/Klassen-Bahnübersichten wurden visuell kontrolliert. Die finale OBB-Geometrie ist für
alle 1.500 Labeldateien exakt identisch zum zunächst mit vertauschten Anzeigenamen erzeugten
Lauf `Ql1i_OBB_20260812`; nur Klassennamen und Dateinamen wurden korrigiert. Der erste Lauf
bleibt zu Nachweiszwecken erhalten, soll aber nicht als finaler Datensatz ausgewählt werden.

Der finale kuratierte Datensatz liegt unter:

```text
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_YOLO_20260812_final\dataset_20260812_121020
```

Er enthält 1.500 Bilder und ist winkelrein aufgeteilt: Train 900 Bilder bei 15,5°, 17,0°
und 20,0°; Validation 300 Bilder bei 18,5°; Test 300 Bilder bei 21,0°. Jeder Split enthält
gleich viele Pose-1-, Pose-2- und Leerbilder.

Das YOLO26n-OBB-Training verwendete 640 Pixel, Batch 16, acht Worker, maximal 75 Epochen und
Patience 15. Early Stopping beendete es nach Epoche 26 und wählte Epoche 11. Der vollständige
Trainingslauf mit Checkpoints, Plots und Auswertungen bleibt hier erhalten:

```text
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_YOLO_20260812\runs\Ql1i_20260812
```

Die Trainingsbilder und Klassen-IDs dieses Laufs sind identisch zum final benannten Datensatz;
nur die im ursprünglichen Run eingebetteten Anzeigenamen waren vertauscht. Der empfohlene
Checkpoint wurde deshalb ohne Änderung der Gewichte mit historisch korrekten Namen gespeichert:

```text
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_YOLO_20260812_final\Ql1i_best.pt
C:\Users\Administrator\Pictures\Ql1i new\Ql1i_best_20260812.pt
```

Beide Kopien besitzen SHA-256
`B25B0280084CA49157223870A36BCD3645108202890A455E403BA14450421BE9` und laden als
OBB-Modell mit `0 = Pose 2`, `1 = Pose 1`.

Validation: Precision 0,97309, Recall 0,99782, mAP50 0,99485 und mAP50–95 0,86961.
Unabhängiger 21°-Test: Precision 0,98084, Recall 0,98638, mAP50 0,99490 und mAP50–95
0,80034. Klassenweise beträgt mAP50–95 0,70340 für Pose 2 (Klassen-ID 0, im ursprünglichen
Run noch als Pose 1 bezeichnet) und 0,89728 für Pose 1 (Klassen-ID 1, dort noch als Pose 2
bezeichnet).

Der Bild-für-Bild-Test bei der GUI-Konfidenz 0,25 ergab 100/100 korrekte Pose-1-Bilder,
100/100 korrekte Pose-2-Bilder und 100/100 korrekte Leerbilder ohne Klassenverwechslung oder
Fehlalarm. 14 positive Bilder erzeugen zusätzlich eine niedrigere zweite Box. Eine höhere
Schwelle entfernt nur einen Teil dieser Zusatzboxen, verliert aber echte Treffer; deshalb ist
0,25 für das neue Netz die empfohlene Schwelle.

Der Vergleich ist deutlich besser als beim Modell vom 9. August: dessen eigener unabhängiger
Test erreichte mAP50 0,86166 und mAP50–95 0,60046. Auf exakt den 300 neuen Testbildern erkennt
das alte Modell bei Konfidenz 0,25 semantisch korrekt nur 43/100 Pose 1 und 90/100 Pose 2;
das neue Modell erreicht 100/100 und 100/100. Beide Modelle erzeugen auf den 100 neuen
Leerbildern keinen Fehlalarm.

Nach jedem Training bleiben `best.pt`, Ultralytics-Plots, Validation-/Testmetriken,
klassenweise Ergebnisse, Confusion-Matrizen, Leerbild-Fehlalarmrate und Zusammenfassung
erhalten. `.pt`-Dateien sind absichtlich in `.gitignore` und müssen separat gesichert werden.

Klassennamen in der Trainingszusammenfassung werden aus `dataset_manifest.json` gelesen. Eine
frühere hart codierte Zwei-Klassen-Annahme wurde beim Kk1a-Lauf entfernt; damit erscheinen
auch Pose 3, Pose 4 und weitere Klassen korrekt in den klassenweisen JSON-Metriken.

