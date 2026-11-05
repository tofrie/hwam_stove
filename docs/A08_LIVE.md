# A08 | Private Live-Anzeige in rc13

Die vorhandene Timestamp-Entität zeigt den erwarteten Zeitpunkt der nächsten beobachtbaren HWAM-Nachlegeanforderung. Die Quellen sind experimentell. Ein gültiger aktueller A04-Zeitpunkt aus dem laufenden Beobachtungsfenster hat Vorrang. Sonst verwendet die Anzeige einen bestätigten HWAM-Countdown. Die Herkunft steht in `prediction_source`, die zugehörige Modellkennung in `model_version`.

## Offline-Befund zum 5. November 2026

Zwei lokale A07-Exporte enthalten 4.869 und 9.842 Zeilen. Identische Sequenzen wurden auf vollständige Gleichheit geprüft. Die erste Datei ist vollständig in der zweiten enthalten. Es bleiben 9.842 eindeutige Zeilen, davon 57 Lückenmarker und 9.785 Beobachtungen vom 2. bis 5. November. In den Messwerten sind 315 harte und 140 kurze Lücken sowie 669 Intervalle mit unsicheren Flanken markiert. Die Auswertung verwendet die tatsächlichen UTC-Beobachtungszeiten.

Die vier Heizsessions erreichten Temperaturspitzen von 336, 357, 347 und 360 °C. Die erste begann für den Beobachter bereits in Burn. Bei drei Sessions sind Ignition und die spätere Rückkehr nach Standby sichtbar. Auch diese enthalten Lücken. 7.192 Beobachtungen stammen aus Burn, 84 aus Glow. Je ein direkter Wechsel Burn → Glow und Glow → Burn ist sichtbar.

Zehn Anforderungsflanken sind kontinuierlich beobachtet, eine weitere liegt hinter einer kurzen Lücke. Die zehn traten bei 200–230 °C, 17–19 % Sauerstoff und Ventilstellungen 100/100/0 % auf. Die einzelnen Wertebereiche überlappen denen von 245 Burn-Beobachtungen mit anschließend fünf beobachteten Minuten ohne Anforderung. Diese eng benachbarten Kontrollpunkte sind statistisch abhängig. Ein zusätzlicher Vorhersagegewinn durch Sauerstoff oder Ventile ist damit offen.

A04 extrapoliert einen zehnminütigen Temperaturgradienten auf 229 °C. Bei Erreichen dieser Temperatur liefert die Rechnung höchstens sehr wenig Vorlauf. Je nach Temperaturverlauf können fehlende Historie, eine steigende Temperatur, mehr als 30 Minuten rechnerischer Vorlauf oder ein verbrauchter Zyklus die Ausgabe verhindern. Im strengen Replay ohne unsichere Flanken gab es kein vollständiges Zehnminutenfenster mit positiver, zulässiger Restzeit. Bei rein numerischer Betrachtung mit bis zu 30 Sekunden Abstand gab es 550 solche **Messpunkte**, aber auch 896 mit bereits unterschrittenem Ziel, 1.205 ohne Abkühlung und 1.293 außerhalb des Horizonts. Das sind Möglichkeiten der Formel, keine unabhängigen Vorhersageereignisse.

Die vom Nutzer berichtete Holzgabe in der letzten Session ist ungefähre externe Evidenz. Der Abstand zur beobachteten Türöffnung lässt die Zuordnung offen. Für weitere frühe oder verspätete Holzgaben fehlen bestätigte Labels.

## Zeitbasis und Auswahl

pystove rc3 setzt die Dauer aus Controllerstunden und -minuten zusammen. Daneben berechnet es einen absoluten Zeitpunkt aus der Ofenuhr. A08 verwendet ausschließlich Beobachtungs-UTC plus Dauer und rundet den sichtbaren HWAM-Zeitpunkt auf Minuten. Das ursprüngliche native Timestamp-Entity und der A04.3-Benchmark akzeptieren nur Glow. Der bereits existierende Dauer-Sensor liefert die Dauer auch in Burn. Die experimentelle Nutzung dieser Dauer in Burn ist eine ausdrücklich separate A08-Anzeigepolitik.

Zwei frische, geschlossene, alarmfreie Beobachtungen derselben aktiven Phase müssen mindestens für die erste HWAM-Ausgabe konsistent sein. Bei gleichem Minutenwert gilt der Zeitpunkt der ersten Beobachtung. Spätere identische Werte halten dieses Ziel fest. Ein neuer Minutenwert kann das Ziel vor- oder zurückverlegen. Abweichungen über fünf Minuten erfordern erneut Bestätigung. Bei der Bestätigung dürfen sich die beiden Zielzeiten um höchstens zwei Minuten unterscheiden. Das sind Plausibilitätsgrenzen der Anzeige, keine geschätzten Firmwareparameter. Eine echte größere Revision kann nach Bestätigung übernommen werden.

Das Anzeigefenster umfasst positive ganze Controller-Minuten bis 180. In Burn wird der mehrdeutige Wert 120 ausgelassen (1.650 Vorkommen). Der größte Burn-Rohwert betrug 14.276 Minuten. Die Grenzen verhindern die Übernahme solcher Werte. Die bestehende A04-Quelle verwendet unverändert ihren eigenen absoluten Ausgabezeitpunkt, Kühlgradienten und Zeithorizont. A08 trainiert online keine Parameter.

Jeder neue reguläre Read wird ausgewertet. Ein identisches Statusmapping kann die HA-Anzeige über den Event Loop aktualisieren. Ein Command-Readback ist keine Modellbeobachtung. A06-Alterssignale dürfen nur die Gültigkeit ändern. Ein Wert über seinem Zeitpunkt ist abgelaufen. Offene oder unbekannte Tür, fehlendes Alarmsignal, Phasenwechsel, unsichere Abstände oder Uhrsprung trennen die Fenster. Eine frische aktive Anforderung erhält Vorrang. Nach Türschluss, Alarmende oder neuer Phase können neue Messungen wieder eine Prognose begründen. Der Live-Pfad zählt keine Holzgaben und bewertet keine unterbrochenen Vorhersagen als Treffer oder Fehler.

## Wiedergabe und Genauigkeit

Der enthaltene Offline-Replay liest Exportdateien in zeitlicher Reihenfolge. Zukunftsdaten werden nur zur nachträglichen Bewertung herangezogen. Zum Ausführen im Projekt mit der HA-Testumgebung: `PYTHONPATH=. python scripts/replay_a08.py ERSTER_EXPORT.json ZWEITER_EXPORT.json`. Die Exporte enthalten keine vollständigen zeitgleichen A04-Issuances. Daher zeigt diese Wiedergabe die HWAM-Quelle separat. Die Quellenauswahl mit tatsächlich aktiver A04-Issuance wird in deterministischen Integrationstests geprüft.

In 1.184 von 6.624 Burn-/Glow-Beobachtungen mit geschlossener Tür und inaktiver Anforderung gab die HWAM-Wiedergabe einen akzeptierten Zeitpunkt aus (17,9 %). Die angezeigten Controllerdauern reichten von 1 bis 180 Minuten. Die drei häufigsten Grenzen im Betrieb sind fehlende oder unplausible Werte, der konstante Burn-Wert 120 und unterbrochene Beobachtungsfenster.

Fünf der zehn sicheren Flanken hatten im unmittelbar vorausgehenden geschlossenen, lückenlosen Fenster eine gültige HWAM-Ausgabe. Die jeweils **letzte** Ausgabe lag um 23,4 / 7,4 / 22,6 / 43,9 / 24,8 Minuten später als die beobachtete Anforderung. Die erste vergleichbare Ausgabe dieser fünf Fenster lag rund 0,5–3,3 Minuten vor der Flanke. Für die Zielhorizonte 5, 15, 30, 45, 60, 90 und 120 Minuten gab es unter diesen Vergleichsbedingungen keine auswertbaren Zeitpunkte. Frühere Ausgaben über Lücken hinweg sind keiner „nächsten“ Anforderung sicher zuzuordnen.

Separat betrachtet hatte die zeitlich letzte Session am 4. November drei sichere Flanken, davon eine bewertbar (+24,8 Minuten). Alle Sessions wurden für die Plausibilitätsanalyse eingesehen. Das ist eine zeitlich aufgeschlüsselte Rückschau, kein verblindeter unabhängiger Genauigkeitsnachweis. Insbesondere belegen weder das 180-Minuten-Anzeigefenster noch häufigere Aktualisierungen eine Genauigkeit 30–120 Minuten vor einer Anforderung. Die begrenzte reale Abdeckung bleibt bei der privaten Validierung sichtbar.

## Lebenszyklus und manuelle Installation

Der reguläre Zustand ist nur bei einer frischen, gültigen Prognose ein Timestamp. Sonst zeigt HA `unknown` mit letztem Ziel, Quelle, `prediction_status`, Begründung, Berechnungszeit und Datenalter. Ein eigener kleiner atomarer HA-Store enthält nur den jüngsten Ausgabedatensatz. Die erste Ausgabe wird sofort gespeichert, spätere Änderungen gebündelt beim regulären Read (höchstens alle fünf Minuten) und abschließend bei Unload oder Shutdown. Ein Prozessabsturz kann seit dem letzten Checkpoint eingegangene Revisionen verlieren. Nach dem Laden ist das Ziel stale oder expired. Erst neue Beobachtungen erlauben wieder einen aktiven Zustand. Ein Fehler im Live-Checkpoint wird separat als `live_storage_healthy=false` angezeigt. Die historischen A02-/A04-/A07-Daten werden hierfür nicht migriert.

1. HA-Backup und Kopie des rc12-Integrationsordners sichern. Überschreiben durch HACS verhindern.
2. ZIP-SHA prüfen, Core manuell stoppen, nur `custom_components/hwam_stove/` ersetzen und Core starten. Den vorhandenen ConfigEntry behalten.
3. rc13, dieselben 49 Entities, reguläre Messwerte und gesunde Stores prüfen. Im Detail der vorhandenen Saynwerk-Nachlegeprognose Quelle und Status ablesen.
4. Rollback: Core stoppen, gesicherten rc12-Ordner wiederherstellen und Core starten. Der separate Live-Store darf liegen bleiben. rc12 ignoriert ihn. Die Dependency bleibt bei `saynwerk-pystove==0.3.0rc3`. H01B bleibt offen.
