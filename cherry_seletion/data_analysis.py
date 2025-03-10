import os
import json
import torch
import argparse
from tqdm import tqdm

import torch.nn as nn
log_softmax = nn.LogSoftmax(dim=-1)
nll_loss = nn.NLLLoss(reduction='none')

if torch.cuda.is_available():
    device = "cuda"
else:
    device = "cpu"

PROMPT_DICT = {
    "prompt_input": (
        "Below is an instruction that describes a task, paired with an input that provides further context. "
        "Write a response that appropriately completes the request.\n\n"
        "### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:"
    ),
    "prompt_no_input": (
        "Below is an instruction that describes a task. "
        "Write a response that appropriately completes the request.\n\n"
        "### Instruction:\n{instruction}\n\n### Response:"
    ),
}

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_path", type=str, required=True)
    parser.add_argument("--save_path", type=str, required=True)
    parser.add_argument("--model_name_or_path", type=str, required=True)
    parser.add_argument("--max_length", type=int, default=512)
    parser.add_argument("--start_idx", type=int, default=0)
    parser.add_argument("--end_idx", type=int, default=-1)
    parser.add_argument("--prompt", type=str, default='wiz', help='wiz, alpaca')
    parser.add_argument("--mod", type=str, default='pre', help='pre, cherry')
    args = parser.parse_args()
    return args

# Used to get the ppl and emb for the whole input
def get_perplexity_and_embedding_whole_text(tokenizer, model, text, max_length):

    input_ids = tokenizer.encode(text, return_tensors="pt", truncation=True, max_length=max_length).to(device)

    with torch.no_grad(): 
        outputs = model(input_ids, labels=input_ids.contiguous())
    loss = outputs.loss
    perplexity = torch.exp(loss)

    hidden_states = outputs.hidden_states
    embeddings = hidden_states[-1]
    sentence_embedding = embeddings.mean(dim=1)

    return perplexity.to('cpu'), sentence_embedding.to('cpu')

# Used to get the ppl and emb for part of input, used in conditional version, and token-wise loss
def get_perplexity_and_embedding_part_text(tokenizer, model, text, target_span, max_length):

    input_ids = tokenizer.encode(text, return_tensors="pt", truncation=True, max_length=max_length).to(device)

    start_index = text.rfind(target_span)
    start_token = len(tokenizer.encode(text[:start_index]))
    end_token = input_ids.shape[1]

    labels = input_ids.clone()
    labels[0, :start_token] = -100

    with torch.no_grad():
        outputs = model(input_ids, labels=labels)

    loss = outputs.loss
    perplexity = torch.exp(loss)

    losses = []
    logits = outputs.logits
    for i in range(1, end_token):
        log_prob_dist = log_softmax(logits[0, i-1])
        true_token = input_ids[0, i]
        token_loss = nll_loss(log_prob_dist.unsqueeze(0), true_token.unsqueeze(0))
        losses.append(token_loss.item())

    return perplexity.to('cpu'), 0, losses


def main():

    args = parse_args()
    print(args)

    from transformers import AutoModelForCausalLM, AutoTokenizer
    model = AutoModelForCausalLM.from_pretrained(args.model_name_or_path, device_map="auto", cache_dir='../cache', output_hidden_states=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name_or_path, cache_dir='../cache')

    tokenizer.chat_template = (
        "{% for message in messages %}"
        "{% if message['role'] == 'system' %}"
        "{{ '<|system|>\n' + message['content'] + '\n' }}"
        "{% elif message['role'] == 'user' %}"
        "{{ '<|user|>\n' + message['content'] + '\n' }}"
        "{% elif message['role'] == 'assistant' %}"
        "{% if not loop.last %}"
        "{{ '<|assistant|>\n'  + message['content'] + eos_token + '\n' }}"
        "{% else %}"
        "{{ '<|assistant|>\n'  + message['content'] + eos_token }}"
        "{% endif %}"
        "{% endif %}"
        "{% if loop.last and add_generation_prompt %}"
        "{{ '<|assistant|>\n' }}"
        "{% endif %}"
        "{% endfor %}"
    )

    model.eval()

    if args.save_path[-3:] != '.pt':
        args.save_path += '.pt'
    if os.path.exists(args.save_path):
        print('save_path exists!')
        raise Exception

    with open(args.data_path, "r") as f:
        data = [json.loads(l) for l in f]

    start_idx = args.start_idx
    end_idx = args.end_idx if args.end_idx != -1 else len(data)
    sampled_data = data[start_idx:end_idx]

    import time
    strat_time = time.time()
    new_data = []
    for i in tqdm(range(len(sampled_data))):

        data_i = sampled_data[i]
        messages_i = data_i['messages']
        whole_text = tokenizer.apply_chat_template(messages_i, tokenize=False)
        # if we have the system first, use the first two turns.
        if messages_i[0]['role'] == 'system':
            instruct_i = tokenizer.apply_chat_template(messages_i[:2], tokenize=False)
            output_i = tokenizer.apply_chat_template(messages_i[2:], tokenize=False)
        else:
            instruct_i = tokenizer.apply_chat_template(messages_i[:1], tokenize=False)
            output_i = tokenizer.apply_chat_template(messages_i[1:], tokenize=False)

        # same for this.
        direct_answer_text = output_i

        temp_data_i = {}
        if args.mod == 'pre':
            ppl_ins_alone, emb_ins_alone = get_perplexity_and_embedding_whole_text(tokenizer, model, instruct_i, args.max_length)
            temp_data_i['ppl'] = [ppl_ins_alone,0,0]
            temp_data_i['sent_emb'] = [emb_ins_alone,0,0]

        elif args.mod == 'cherry':
            instruct_i_input_ids = tokenizer.encode(instruct_i, return_tensors="pt", truncation=True, max_length=args.max_length).to(device)
            instruct_i_len = instruct_i_input_ids.shape[1] 
        
            ppl_out_alone, _, loss_list_alone = get_perplexity_and_embedding_part_text(tokenizer, model, direct_answer_text, output_i, args.max_length-instruct_i_len+4)
            ppl_out_condition, _, loss_list_condition = get_perplexity_and_embedding_part_text(tokenizer, model, whole_text, output_i, args.max_length)

            temp_data_i['ppl'] = [0,ppl_out_alone,ppl_out_condition]
            temp_data_i['token_loss'] = [[],loss_list_alone,loss_list_condition]

        new_data.append(temp_data_i)
        pass

    print('New data len:', len(new_data))
    torch.save(new_data,args.save_path)

    print('Time Used:',(time.time()-strat_time)/60,'(min)')

if __name__ == "__main__":
    main()