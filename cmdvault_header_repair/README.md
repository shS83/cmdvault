# CmdVault: logon ylätilan ja statuspallon korjaus

Tämä päivitys korjaa aiemman help-tekstin sijaintimuutoksen jälkeen hajonneen Kitty-asettelun. Se **ei muuta** `cmdvault.png`-tiedostoa, logon kokoa, kuvasuhdetta eikä neonkehystä.

## Muutokset

- `cmdvault`: palauttaa fzf:lle **6 rivin ylämarginaalin**, kun CmdVaultin logo on käytettävissä ja terminaali riittävän suuri. Jos tämä kohta on jo kunnossa, sitä ei muuteta. Huomioi myös tilanteen, jossa käynnistintiedosto oli palautettu vanhasta `.save`-kopiosta.
- `src/visuals/picker.py`: lopettaa erillisen `ALT-SHIFT-H for help` -tekstin piirtämisen logon lähelle. fzf:n oma pikanäppäinrivi ja Alt-Shift-H-näppäinyhdistelmä toimivat edelleen. Palauttaa `READY`-tekstin alkuperäiseen paikkaansa.
- `src/visuals/kitty.py`: siirtää **vain** Kittyn synkronointipalloa 10 fyysistä pikseliä alaspäin.

## Asennus

Pura paketti haluamaasi hakemistoon (esim. `~/Downloads`). Suorita CmdVaultin projektihakemistossa:

```bash
cd ~/Code/cmdvault
python /polku/cmdvault_header_repair/repair_cmdvault_header.py --project . --dry-run
python /polku/cmdvault_header_repair/repair_cmdvault_header.py --project .
./install.sh
```

Tarkista halutessasi ennen asennusta muutokset:

```bash
git diff -- cmdvault src/visuals/picker.py src/visuals/kitty.py
```

Muutosohjelma tekee muokkaamistaan tiedostoista uniikisti nimetyt `.before-header-repair.bak`-varmuuskopiot. Se tarkistaa kaikki odotetut muutoskohdat ja Python-syntaksin ennen tiedostojen kirjoittamista. Jos koodisi eroaa olennaisesti odotetusta, ohjelma kieltäytyy muuttamasta sitä.

Päivitys on turvallinen ajaa uudelleen: jo korjattuja kohtia ei kopioida kahdesti. Ohjelma ei tee Git-committeja eikä pushia puolestasi.

**Huom:** varsinaista Kittyn animaatiota ei ole tässä ympäristössä voitu suorittaa. Paikkamuutokset on tarkistettu lähdekoodista ja muutostyökalun testiajolla.
