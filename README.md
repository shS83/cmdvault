# cmdvault – pitkien komentojen tarkastelu

`src/visuals/picker.py` on pohjattu Sync Pulse Native Pixel Fix -versioon.

- **→** avaa fzf:n viisirivisen esikatselun, joka näyttää koko valitun komennon rivitettynä.
- **←** sulkee esikatselun ja palauttaa koko tuloslistan.
- Valittu komento ei suoritu esikatselussa: `printf` tulostaa fzf:n turvallisesti lainaaman näytettävän tekstin.
- Sync Pulse / Neon Frame -grafiikkaa ei muutettu.
- Ctrl-R ei ole vielä sidottu synkronointiin: oikeaa historiaa ei saa lähettää ennen eksplisiittistä opt-in-vaihetta.

Asenna projektin juureen varmuuskopion jälkeen:

```sh
cp src/visuals/picker.py src/visuals/picker.py.bak
unzip -o cmdvault-long-command-fix.zip
python -m py_compile src/visuals/picker.py
./cmdvault --graphics kitty
./install.sh
```

Rajoitus: tämä käyttää nuolia esikatselun avaamiseen/sulkemiseen, **ei** varsinaiseen tekstin vaakasiirtoon.
