# scf2wth

Orchestrates the full chain from a seasonal climate forecast to DSSAT-ready
`.WTH` files:

```
scfbridge (plan -> fetch -> render paramPT.txt)
    -> fresampler  (FResampler1_PT: paramPT.txt -> N .WTD realizations)
    -> wtd2wth     (.CLI + all .WTD realizations, ONE call -> .WTH files)
    -> saved into a dedicated output folder for DSSAT experiments
```
