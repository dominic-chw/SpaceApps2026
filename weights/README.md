# weights/

`default.json` says how much each feature counts at each depth band:
`{band: {feature: weight}}`. A feature left out of a band gets weight 1
(if `pipeline/matching/features.yaml` says the feature applies to that band).
Weight 0 switches a feature off.

**The current numbers are PLACEHOLDERS** until Markus picks the final variables.
The reasoning behind them:
- **Ice, ground type and hardness count most**, and more with depth: they decide
  whether a drill gets through and what it brings up.
- **Temperature counts less**: Mars and Earth temperatures both come from the
  same heat model, so giving them big weights would count one thing twice.
- **Frozen share of the year counts more with depth** (1 -> 2): frozen,
  ice-cemented ground is what jams drills, and deeper ground stays frozen longer.
  It replaces comparing plain kelvin, which always mismatched (Mars is 40-100 K colder).
- **Temperature swing fades with depth**: daily and yearly swings die out
  within a metre or two, so it matters near the surface and not in `deep`.
- **Slope and roughness fade with depth**: they matter for setting a rig down,
  less for what is underneath.
- **Dryness only applies near the surface** (`skin`, `upper_metre`).
