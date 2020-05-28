import sys
import os
import time
import argparse
import numpy as np
import random

sys.path.append('../')

from tqdm import tqdm
import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data as data
import torch.backends.cudnn as cudnn
from torch.utils.tensorboard import SummaryWriter
from torch.utils.data import Dataset, DataLoader
from torchvision import models
import torchvision.transforms as transforms
device = 'cuda:0'

# from model import pthVBPR

from PIL import Image
from io import StringIO, BytesIO
import recsys_models
import config

data_train = 'amazon'
if data_train == 'amazon':

    dataset_name = 'AmazonMenWithImgPartitioned.npy'

    dataset = np.load('../data/'+ dataset_name, encoding='bytes')
    [user_train, user_validation, user_test, Item, usernum, itemnum] = dataset
    cold_list = np.load('../data/amazon_one_k_cold.npy')
    alex_4096_cnn_f = np.load('../data/amazon_alexnet_features.npy')
elif data_train == 'tradesy':
    
    dataset_name = 'TradesyImgPartitioned.npy'
    dataset = np.load('../data/' + dataset_name, encoding='bytes')
    [user_train, user_validation, user_test, Item, usernum, itemnum] = dataset
    cold_list = np.load('../data/tradesy_one_k_cold.npy')
    alex_4096_cnn_f = np.load('../data/tradesy_alexnet_features.npy')

    
oneiteration = 0
for item in user_train: oneiteration+=len(user_train[item])
    

def sample(user):
    u = random.randrange(usernum)
    numu = len(user[u])
    i = user[u][random.randrange(numu)][b'productid']
    M=set()
    for item in user[u]:
        M.add(item[b'productid'])
    while True:
        j=random.randrange(itemnum)
        if (not j in M) and (not j in cold_list): break
    return (u,i,j, alex_4096_cnn_f[i], alex_4096_cnn_f[j])

VBPRmodel = recsys_models.pthVBPR(usernum, itemnum, 100, 4096)
VBPRmodel.train().to(device)


class trainset(Dataset):
    def __init__(self):
        self.target = train_ls

    def __getitem__(self, index):
        target = self.target[index]
        return target[0], target[1], target[2], target[3], target[4]

    def __len__(self):
        return len(self.target)

class testset(Dataset):
    def __init__(self):
        self.target = test_ls

    def __getitem__(self, index):
        target = self.target[index]
        return target[0], target[1], target[2], target[3], target[4]

    def __len__(self):
        return len(self.target)
    
class valset(Dataset):
    def __init__(self):
        self.target = val_ls

    def __getitem__(self, index):
        target = self.target[index]
        return target[0], target[1], target[2], target[3], target[4]

    def __len__(self):
        return len(self.target)
    
train_ls = [list(sample(user_train)) for _ in range(oneiteration)]
train_data  = trainset()

def sample_val(u_idx):
    u = u_idx
    user = user_validation[u_idx]
    i = user[random.randrange(1)][b'productid']
    M=set()
    for item in user:
        M.add(item[b'productid'])
    while True:
        j=random.randrange(itemnum)
        if (not j in M) and (not j in cold_list): break
    return list((u,i,j, alex_4096_cnn_f[i], alex_4096_cnn_f[j]))

val_ls = [list(sample_val(u_idx) for i in range(100)) for u_idx in tqdm(range(usernum))]
val_ls = np.array(val_ls).reshape(-1, 5)

val_data  = valset()

val_loader = DataLoader(val_data, batch_size = 100, 
                       shuffle = False, num_workers = 2)


def metrics_VBPR(model, val_loader, top_k):
    HR, NDCG = [], []

    for user, item_i, item_j, cnn_i, cnn_j in val_loader:
        user = user.to(device)
        
        item_i = item_i.to(device)
        item_j = item_j.to(device)
        cnn_i = cnn_i.to(device)
        cnn_j = cnn_j.to(device)

        prediction_i = model(user, item_i, cnn_i)
        prediction_j = model(user, item_j, cnn_j)
        to_rank = np.append(prediction_j.detach().cpu().numpy(), prediction_i[0][0].detach().cpu().numpy())
        _, indices = torch.topk(torch.tensor(to_rank), top_k)
        if 100 in indices:
            HR.append(1)
        else:
            HR.append(0)
    return np.mean(HR)



# parameter for amazon
optimizer = optim.Adam(VBPRmodel.parameters(), lr=1e-3, weight_decay=1e-4)

# parameter for tradesy k20 
# optimizer = optim.Adam(VBPRmodel.parameters(), lr=1e-3, weight_decay=1e-5)

# lr=1e-2 does not work for both tradesy and amazon

# optimizer = optim.Adam(VBPRmodel.parameters(), lr=1e-3, weight_decay=1e-5)
# optimizer = optim.Adam(VBPRmodel.parameters(), lr=1e-1)

# writer = SummaryWriter() # for visualization



writer = SummaryWriter()
writer_ct = 0

count, best_hr = 0, 0
for epoch in tqdm(range(2000)):
    VBPRmodel.train() 
    start_time = time.time()
    train_ls = [list(sample(user_train)) for _ in range(oneiteration)]
    train_data  = trainset()

    for data in DataLoader(train_data, batch_size = 512, 
                       shuffle = False, pin_memory = True, num_workers = 4):
        
        user, item_i, item_j, cnn_i, cnn_j = data

        VBPRmodel.zero_grad()
        prediction_i = VBPRmodel(user.to(device), item_i.to(device), cnn_i.to(device))
        prediction_j = VBPRmodel(user.to(device), item_j.to(device), cnn_j.to(device))
        loss = - (((prediction_i - prediction_j)).exp() + 1).sigmoid().mean()
        
        loss.backward(retain_graph=True)
        optimizer.step()
        
        writer.add_scalar('vbprruns/loss', loss.item(), count)
        count += 1
    VBPRmodel.eval()
    HR = metrics_zrl(VBPRmodel, val_loader, 5)
    writer.add_scalar('vbprruns/hr5', HR, count)
    elapsed_time = time.time() - start_time
    
    print("The time elapse of epoch {:03d}".format(epoch) + " is: " + 
            time.strftime("%H: %M: %S", time.gmtime(elapsed_time)))
    print("HR: {:.3f}".format(np.mean(HR)))

    if HR >= best_hr:
        best_hr, best_epoch = HR, epoch
        if True:
            if not os.path.exists(config.model_path):
                os.mkdir(config.model_path)
            torch.save(VBPRmodel, '{}best_k100_amazon_VBPR_trial.pt'.format(config.model_path))

    print("End. Best epoch {:03d}: HR = {:.3f}".format(best_epoch, best_hr))