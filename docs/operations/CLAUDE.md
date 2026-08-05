# The model



## Methods

Methods are found in `../methods` but can be summarized as an explore/exploit approach. If parts are not further elaborated, we implemented the most vanilla of it.

Default setting includes:
- Cache-reload if same request
- PoBB for early elimination of unfit individuals
- An adaptive hard-sample-sorter as to select the samples with the highest Signal-to-Noise ratio to boost PoBB even further ([Rasch-guided](../concepts/adaptive-queue-mechanism.md))

###  PoBB

Early-elimination of unfit individuals

- `elimination_n_min` is the number of cells a candidate must run before PoBB is allowed to eliminate it — the floor that stops a variant being cut on one unlucky draw. 