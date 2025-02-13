import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--mean_list_paths", type=str, nargs='+', required=True)
parser.add_argument("--data_json_paths", type=str, nargs='+', required=True)
parser.add_argument("--save_path", type=str, required=True)
parser.add_argument("--sample_num", type=int, default=326000)
args = parser.parse_args()

# step 1, load all the mean list
# we need to adjust the ids to our full data list
# assuming data and mean list in same order, seems reasonable.
mean_list = []
ongoing_id = 0
for mean_list_path in args.mean_list_paths:
    with open(mean_list_path, "r") as f:
        for l in f:
            mean_list.append([json.loads(l)[0], ongoing_id])
            ongoing_id += 1
    
    
print('mean_list len:', len(mean_list), ". Now Sorting")
mean_list = sorted(mean_list)
mean_rate_list_id = [i for i in range(len(mean_list))][-args.sample_number:]
mean_rate_list_id_sample = [mean_list[id][1] for id in mean_rate_list_id]
mean_rate_list_id_sample = sorted(mean_rate_list_id_sample)

# load all the json data
json_data = []
for data_json_path in args.data_json_paths:
    with open(data_json_path, "r") as f:
        json_data += [json.loads(l) for l in f]


new_data = [json_data[idx] for idx in mean_rate_list_id_sample]
print('New data len \n',len(new_data))
with open(args.save_path, "w") as fw:
    for data_i in new_data:
        fw.write(json.dumps(data_i) + "\n")
