# Kombi – Schulden- und Verleih-Tracker mit dynamischem Verzugsausgleich

Multi-User-Web-App mit RBAC, Admin-Panel und Kombi-Engine (fixer Wochenaufpreis, Deckel, Teilzahlungen, Geräteückgaben).
Oberfläche komplett Deutsch (1.234,56 €, TT.MM.JJJJ, Europe/Berlin).

## Stack
| Schicht | Technik |
|---|---|
| Web | Python 3.12, Flask 3 (serverseitig gerenderte Jinja-Templates, kein Build-Schritt), Gunicorn |
| DB | PostgreSQL 16 über SQLAlchemy 2 (Tests: SQLite) |
| Auth | Argon2id (argon2-cffi), signierte Session-Cookies (HttpOnly, SameSite=Lax, Secure) |
| Mail | smtplib; SMTP-Passwort in der DB per Fernet verschlüsselt |
| Betrieb | Multi-Stage-Dockerfile (Nutzer UID 10001), `docker-compose.yml` (lokal), `stack.yml` (Swarm/Portainer) |

## Ordnerstruktur
```
app/engine.py        Kombi-Engine (reine Funktionen, Ganzzahl-Cent)
app/models.py        Datenmodell      app/migrations.py  versionierte Migrationen (Auto-Start)
app/security.py      Argon2, CSRF, RBAC-Decorators, DB-Rate-Limit
app/auth.py          Login, Registrierung (optional), Reset, Einladung
app/main.py          Dashboard, Einträge, Tilgung, Kontakte, Einstellungen, Support-Code
app/admin.py         Nutzer, Konfiguration, E-Mail, Statistik, Support-Zugriff
app/templates, app/static
tests/               test_engine.py (Abschnitt 7), test_tenancy.py (Mandantentrennung, Support)
Dockerfile, docker-compose.yml, stack.yml, .env.example
```

## Datenmodell (Kurzform)
`users` · `contacts` (je Nutzer, ohne Verknüpfung zu Konten) · `entries` (Richtung, Kategorie, Kontakt, Leihdatum,
Hauptschuld in Cent, Stückzahl, Wochensatz, Deckel-Art/-Wert, Ausschluss-Flag) · `week_overrides` · `payments`
(Geldzahlung oder Rückgabe, Verrechnungsreihenfolge) · `settings` · `tokens` (Einladung/Reset, nur Hash) ·
`support_grants` · `support_log` · `rate_limits`. Der Saldo wird **nie gespeichert**, sondern bei jeder Anfrage berechnet.

## Annahmen
1. Stack-Wahl Flask + PostgreSQL + Jinja; Live-Vorschau per kleinem Vanilla-JS, das den Server-Endpunkt `/entries/preview` nutzt (eine Rechenlogik).
2. Geldzahlungen werden chronologisch gegen den **heute** aufgelaufenen Aufpreis und die aktuelle Hauptschuld verrechnet. Die Reihenfolge beeinflusst nur die Aufteilung, nie den Saldo. Änderungen wirken rückwirkend.
3. Geräte: Stückzahl in „g“ = Geräteanzahl. Hauptschuld-Reduktion bei Rückgabe = Gesamtwert × zurückgegebene Stücke ÷ Stückzahl (ganzzahlig, kumulativ gerundet, damit das letzte Stück exakt aufgeht). Geräte können zusätzlich per Geld getilgt werden.
4. Wochen-Deckel: Wochen nach dem Deckel zählen nicht. Überschreibungen für Wochen jenseits der angerechneten Wochen werden ignoriert. Betrags-Deckel begrenzt die Summe inkl. Überschreibungen.
5. Leihdatum in der Zukunft → 0 angerechnete Wochen. Zahlungsdaten dürfen nicht in der Zukunft liegen.
6. Dashboard-Summen enthalten alle Einträge; das Ausschluss-Flag wirkt nur auf den Kontakt-Gesamtsaldo.
7. Admins sind zugleich normale Nutzer mit eigenen Daten, sehen aber fremde Daten nur über den Support-Code. Fremde IDs liefern 404 (keine Existenz-Preisgabe).
8. Statistik: „Verleihungen gesamt“ = Summe aller Hauptschulden (beide Richtungen, Geräte mit Wiederbeschaffungswert); „Rückzahlungen“ = Geldzahlungen + Wert zurückgegebener Geräte; „Aktive Nutzer“ = nicht gesperrt und mit gesetztem Passwort.
9. Support-Code: 6 Ziffern, 24 h gültig, nur als HMAC-artiger SHA-256-Hash (mit SECRET_KEY und Nutzer-ID) gespeichert, einmal einlösbar. Rate-Limit: 5 Fehlversuche/15 min je Admin und 10/h je Ziel. Die Sitzung endet bei Widerruf, Ablauf oder „Sitzung beenden“. Der Nutzer sieht im Protokoll Zeit, Ereignis, Admin und IP.
10. Kontakte lassen sich nur löschen, wenn keine Einträge mehr existieren.
11. Passwort-Mindestlänge 10 Zeichen. Passwortänderung/Sperre/Reset beendet alle bestehenden Sitzungen.
12. Einladungslinks werden nie im Admin-Panel angezeigt (sonst könnte der Admin das Konto übernehmen); schlägt der Mailversand fehl, kann die Einladung erneut gesendet werden.
13. Der SMTP-Schlüssel wird aus `SECRET_KEY` abgeleitet – ein Wechsel des Session-Keys macht ein in der DB gespeichertes SMTP-Passwort unlesbar (dann neu eintragen oder `SMTP_PASSWORD` nutzen).
14. Migration `0001_baseline` legt das Schema an; spätere Änderungen als neue, idempotente Migration in `app/migrations.py` ergänzen.

## Einschränkungen
- Keine Ende-zu-Ende-Verschlüsselung (nicht gefordert); Mandantentrennung erfolgt per Zugriffskontrolle.
- Die Admin-Statistik berechnet in Python über alle Einträge – für private Größenordnungen ausreichend, nicht für Millionen Zeilen.
- Hinter Swarm-Ingress (`mode: ingress`) sieht die App oft nur die Ingress-IP; für echte Client-IPs im Rate-Limit/Protokoll den Reverse Proxy im selben Overlay-Netz betreiben (Traefik-Beispiel in `stack.yml`).
- Kein Export/Import, keine Mehrsprachigkeit, kein 2FA.

## Konfiguration (Umgebungsvariablen; jeweils auch als `NAME_FILE` für Docker Secrets)
| Variable | Bedeutung | Standard |
|---|---|---|
| `SECRET_KEY` | Session-Signatur (min. 16 Zeichen) – **Secret** | – (Pflicht) |
| `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` | Datenbank | `db`/`5432`/`kombi`/`kombi` |
| `DB_PASSWORD` | DB-Passwort – **Secret** | – |
| `DATABASE_URL` | Alternative zu den DB_*-Werten | – |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Erster Admin (nur wenn noch keiner existiert) – Passwort als **Secret** | `admin` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_FROM`, `SMTP_PASSWORD` | Mail-Fallback; im Admin-Panel überschreibbar | – |
| `APP_NAME`, `BASE_URL` | Name und öffentliche URL für Mail-Links | `Kombi` |
| `COOKIE_SECURE` | Cookie-Flag `Secure` | `true` |
| `TRUST_PROXY` | `X-Forwarded-*` auswerten (ProxyFix) | `true` |
| `PORT` | Port im Container (veröffentlichter Port: `APP_PORT` in der Stack-Datei) | `8000` |
| `SEED_DEMO` | Beispieldaten (Nutzer `demo`, Passwort `DEMO_PASSWORD`) | `false` (compose: `true`) |
| `DB_RETRIES`, `DB_RETRY_DELAY` | Warten auf die DB | `60`, `2` s |

## Deployment

### 1. Lokal
```bash
docker compose up --build
# http://localhost:8000   Admin: admin / admin-local-pass   Demo: demo / demo-passwort-123
```

### 2. Image bauen und pushen
```bash
docker build -t <REGISTRY>/<NAMESPACE>/kombi:<TAG> .
docker login <REGISTRY>
docker push <REGISTRY>/<NAMESPACE>/kombi:<TAG>
# Mehrere Architekturen: docker buildx build --platform linux/amd64,linux/arm64 -t <REGISTRY>/<NAMESPACE>/kombi:<TAG> --push .
```

### 3. Stack in Portainer
1. Secrets anlegen (Portainer → Secrets → Add secret, oder per CLI auf einem Manager):
   ```bash
   openssl rand -base64 48 | docker secret create kombi_secret_key -
   openssl rand -base64 32 | docker secret create kombi_db_password -
   printf '%s' 'EIN-STARKES-ADMIN-PASSWORT' | docker secret create kombi_admin_password -
   printf '%s' 'smtp-passwort-oder-platzhalter' | docker secret create kombi_smtp_password -
   ```
2. Falls die Registry privat ist: unter Registries hinterlegen und beim Stack „Registry-Zugang“ aktivieren (`--with-registry-auth`).
3. Stacks → Add stack → Web editor → Inhalt von `stack.yml` einfügen.
4. Unter *Environment variables* mindestens `APP_IMAGE`, `ADMIN_EMAIL`, `BASE_URL`, `DB_PLACEMENT` (z. B. `node.hostname == mein-knoten`) eintragen (siehe `.env.example`). Deploy.
5. Die Datenbank ist per `placement.constraints` an einen Knoten gebunden, weil das Volume `db_data` lokal liegt. Für Betrieb ohne feste Bindung ein Netzwerk-Volume (NFS) oder einen externen Postgres nutzen (`DB_HOST`/`DATABASE_URL`).

### 4. Admin, Proxy, Backup
- **Erster Admin:** entsteht beim ersten Start aus `ADMIN_USERNAME`/`ADMIN_EMAIL`/Secret `kombi_admin_password`. Danach anmelden, Passwort in den Einstellungen ändern, SMTP unter *Admin → E-Mail* eintragen und Testmail senden.
- **Reverse Proxy:** Weiterleitung auf Port `APP_PORT` (oder Traefik-Labels aus `stack.yml`). Der Proxy muss `X-Forwarded-Proto`, `-Host`, `-For` setzen. Prüfen: `https://<domain>/health` → `{"status":"ok"}`, Login funktioniert (Cookie ist `Secure`).
- **Backup (täglich per Cron auf dem DB-Knoten):**
  ```bash
  docker exec $(docker ps -q -f name=<STACKNAME>_db) pg_dump -U kombi kombi | gzip > kombi_$(date +%F).sql.gz
  ```
  Wiederherstellung: `gunzip -c kombi_DATUM.sql.gz | docker exec -i $(docker ps -q -f name=<STACKNAME>_db) psql -U kombi kombi`.
  Alternativ das Volume sichern: `docker run --rm -v <STACKNAME>_db_data:/v -v $PWD:/b alpine tar czf /b/db_data.tgz -C /v .` (DB vorher stoppen).

## Tests
```bash
pip install -r requirements-dev.txt
pytest -q
```
`tests/test_engine.py` deckt alle acht Rechenbeispiele aus Abschnitt 7 ab, `tests/test_tenancy.py` Mandantentrennung, Admin-Isolation, CSRF und den kompletten Support-Code-Ablauf (Einmaligkeit, Ablauf, Widerruf, Rate-Limit).
