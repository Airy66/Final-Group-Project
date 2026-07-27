# Required Datasets (ZT-FaaSGuard)

## 1) Azure Functions Dataset 2019 (official)
- Doc: https://github.com/Azure/AzurePublicDataset/blob/master/AzureFunctionsDataset2019.md
- Tarball: https://azurepublicdatasettraces.blob.core.windows.net/azurepublicdatasetv2/azurefunctions_dataset2019/azurefunctions-dataset2019.tar.xz

Recommended files:
- invocations_per_function_md.anon.d01.csv ... d14.csv
- function_durations_percentiles.anon.d01.csv ... d14.csv
- app_memory_percentiles.anon.d01.csv ... d12.csv

## 2) EUA Melbourne CBD dataset
- Repo: https://github.com/PhuLai/eua-dataset
- ZIP: https://github.com/PhuLai/eua-dataset/archive/refs/heads/master.zip
- Edge servers (125 nodes):
  https://raw.githubusercontent.com/PhuLai/eua-dataset/master/edge-servers/site-optus-melbCBD.csv
- Users (816 points):
  https://raw.githubusercontent.com/PhuLai/eua-dataset/master/users/users-melbcbd-generated.csv

## 3) CICIoT2023
- Official: https://www.unb.ca/cic/datasets/iotdataset-2023.html
- Official download form: https://cicresearch.ca/IOTDataset/CIC_IOT_Dataset2023/
- Non-official mirror: https://www.kaggle.com/datasets/himadri07/ciciot2023

## Note
In this environment, remote downloads may fail due to proxy/network restrictions.
Mount datasets locally, then pass file paths to `experiments/run_all.py`.
