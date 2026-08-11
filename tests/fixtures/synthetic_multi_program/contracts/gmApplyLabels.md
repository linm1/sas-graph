# gmApplyLabels

## Purpose

Applies a label set to an input dataset and writes a labelled copy.

## Parameters

| Parameter | Description | Acceptable Values | Default Value |
|---|---|---|---|
| inds | Input dataset. | LIBRARY.DATASET | REQUIRED |
| outds | Output dataset. | LIBRARY.DATASET | REQUIRED |

## Examples

%gmApplyLabels(inds=work.summary_out, outds=work.summary_labeled);
