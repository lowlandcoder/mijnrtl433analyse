# MijnRTL433-analyse

Afgeschermde analysepagina van de RTL433-ontvangst
(`mijnrtl433analyse.lab023.nl`). Leest `events.jsonl` (de `-F json`-uitvoer van
de rtl433-container) en toont een rapport met grafieken en een omgevingskaart:
welke apparaten in de buurt zenden, temperatuur- en verkeerspatronen, en
aandachtspunten. Een knop "Ververs" maakt het rapport opnieuw op basis van de
dan aanwezige data.

Dit is de halfautomatische opzet: het rapport wordt niet op een schema
bijgewerkt, maar wanneer op de knop wordt gedrukt.

## Onderdelen

- `analyse.py` — de backend: leest `events.jsonl`, maakt de grafieken en het
  rapport, en biedt de pagina met de verversknop.
- `Dockerfile`, `requirements.txt`, `docker-compose.yml` — om de container te
  bouwen en te draaien op de sdr-server.
- `nginx-mijnrtl433analyse.conf` — doorschakeling met centrale aanmelding, op de
  lab023-server.

## Werking

De analysecontainer draait op de sdr-server, waar de rtl433-container de
metingen naar `events.jsonl` schrijft. De analyse leest datzelfde bestand
alleen-lezen via een gedeeld volume; er hoeft dus niets gekopieerd te worden.
Bij het openen van de pagina, en bij elke druk op "Ververs", wordt het rapport
opnieuw gemaakt uit de actuele data.

## Inrichting op de sdr-server

1. Het hostpad van de rtl433-data opzoeken:

       docker inspect rtl433 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'

   Neem de `Source` die hoort bij `/data`.
2. Dat pad invullen in `docker-compose.yml`, op de regel met `:/data:ro`.
3. Bouwen en starten:

       sudo docker compose up -d --build

   De pagina luistert nu op poort 8300 van de sdr-server.

## Inrichting op de lab023-server

1. DNS-regel voor `mijnrtl433analyse.lab023.nl`, zoals bij de andere
   subdomeinen.
2. De nginx-conf plaatsen en activeren:

       sudo cp nginx-mijnrtl433analyse.conf /etc/nginx/sites-available/mijnrtl433analyse
       sudo ln -s /etc/nginx/sites-available/mijnrtl433analyse /etc/nginx/sites-enabled/
       sudo nginx -t && sudo systemctl reload nginx

3. HTTPS instellen:

       sudo certbot --nginx -d mijnrtl433analyse.lab023.nl

   Controleer daarna dat `include snippets/lab023-login.conf;` in het 443-blok
   staat.
4. Op `mijnsdr.lab023.nl` staat de kaart MijnRTL433-analyse al klaar (repository
   `mijnsdr`).

## Aandachtspunten

- Met één stick verzamelt RTL433 alleen data als die container draait; de
  analyse dekt dus alleen die periodes.
- `events.jsonl` groeit onbeperkt. Bij een lange meetreeks is een bewaartermijn
  of rotatie het overwegen waard; de analyse leest anders een steeds groter
  bestand.
- Bandensensoren (TPMS) hebben een unieke, volgbare id. De pagina is daarom
  afgeschermd en voor eigen gebruik.

## Geheimen

Geen geheimen in deze map.
