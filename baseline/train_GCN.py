import os
import random
import time
import copy
import torch
import torch.optim as optim
import torch.nn.functional as F
import pprint as pp
import utils.hypergraph_utils as hgut
import pickle as pkl
import joblib as jl
import numpy as np
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from models import HGNN, GCN, HGNN_time, HGNN_time_2
from config import get_config
from datasets import load_feature_construct_H
import scipy.sparse as ss
import setproctitle
from sklearn import metrics
from sklearn.metrics import precision_recall_curve

seed = 123

torch.manual_seed(seed)
torch.cuda.manual_seed(seed)
torch.cuda.manual_seed_all(seed)  # if you are using multi-GPU.
np.random.seed(seed)  # Numpy module.
random.seed(seed)  # Python random module.
torch.manual_seed(seed)
torch.backends.cudnn.benchmark = False
torch.backends.cudnn.deterministic = True

def remove_nan(data1,data2,data3):#
    re=[]
    prec=[]
    thre=[]
    for i in range(len(data1)):
        if data1[i]!=0 and data2[i]!=0 and data2[i]!=1 and data1[i]!=1:
            re.append(data1[i])
            prec.append(data2[i])
            thre.append(data3[i-1])
    recall=np.array(re)
    precision=np.array(prec)
    thre=np.array(thre)
    return recall,precision,thre

setproctitle.setproctitle("HGNN@")
# os.environ['CUDA_VISIBLE_DEVICES'] = '1'
cfg = get_config('config/GCNconfig.yaml')


if cfg['dataset'] == 'timegeo':
    with open("datasets/G_un=7_rz=True", 'rb') as f:
        G = pkl.load(f)
    with open("datasets/New_label", 'rb') as f:
        lbls = pkl.load(f)
    with open("datasets/fts", 'rb') as f:
        fts = pkl.load(f)
    with open("datasets/Adj", 'rb') as f:
        adj = ss.csr_matrix(pkl.load(f))
elif cfg['dataset'] == 'sim':
    lbls = np.load("/bj-sim/privacy/label.npy")
    # lbls = np.load("/bj-sim/privacy/label_omicron.npy")
    if cfg['fts_type'] == 'time':
        fts = np.load("/bj-sim/privacy/noposterior/trace_array_4h.npy")
        fts = fts + 1
    elif cfg['fts_type'] == 'freq':
        fts = np.load("/bj-sim/privacy/noposterior/visit_frequency.npy")
    adj = np.load("/bj-sim/privacy/noposterior/A_un=10_r01=True.npy")



adj = hgut.preprocess_adj(adj)
adj = adj.todense()
print('load files successfully!')

# setting parameters
train_ratio = 0.4
embedding_dim = 4
region_num = 11459

sample_num = lbls.shape[0]
train_num = int(sample_num*train_ratio)

# for test element-wise product
# G1 = np.random.rand(sample_num, 40000)
# G2 = np.random.rand(40000, sample_num)

idx_train = np.array(range(train_num))
idx_infect = list(np.argwhere(lbls[idx_train].flatten()==1).flatten())
idx_sus = list(np.argwhere(lbls[idx_train].flatten()==0).flatten())
x = idx_infect + idx_sus
idx_test = np.array(range(train_num, sample_num))


# G = hgut.generate_G_from_H(H)
# print('generate G successfully!')
n_class = int(lbls.max()) + 1
device = torch.device('cuda:1')


# transform data to device
fts = torch.LongTensor(fts).to(device)
# 
# torch.nn.init.kaiming_uniform_(fts, mode='fan_in', nonlinearity='relu')
lbls = torch.Tensor(lbls).squeeze().long().to(device)
adj = torch.Tensor(adj).to(device)
parm = {}
parm1 = {}

def train_model(model, criterion, optimizer, scheduler, num_epochs=25, print_freq=50):
    since = time.time()

    best_model_wts = copy.deepcopy(model.state_dict())
    best_acc = 0.0
    best_f1 = 0.0
    best_loss = np.inf
    loss_val = []

    for name, parameters in model.named_parameters():
        parm[name] = parameters.detach().cpu().numpy()
    for epoch in range(num_epochs):
        if epoch % print_freq == 0:
            print('-' * 10)
            print(f'Epoch {epoch}/{num_epochs - 1}')
        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                # scheduler.step()
                model.train()  # Set model to training mode
            else:
                model.eval()  # Set model to evaluate mode

            running_loss = 0.0
            running_corrects = 0
            TP = 0
            TN = 0
            FP = 0
            FN = 0

            idx = idx_train if phase == 'train' else idx_test

            # Iterate over data.
            optimizer.zero_grad()
            with torch.set_grad_enabled(phase == 'train'):

                outputs = model(fts, support)
                loss = criterion(outputs[idx], lbls[idx])
                _, preds = torch.max(outputs, 1)

                # backward + optimize only if in training phase
                if phase == 'train':
                    loss.backward()
                    optimizer.step()
                    scheduler.step()

            # statistics
            running_loss += loss.item() * fts.size(0)
            running_corrects += torch.sum(preds[idx] == lbls.data[idx])
            outputs_pro = F.softmax(outputs)
            with open('preds', 'wb') as f:
                pkl.dump(preds.cpu().detach().numpy(), f)
            with open('outputs_pro', 'wb') as f:
                pkl.dump(outputs_pro.cpu().detach().numpy(), f)

            # TP    predict 和 label 
            TP += ((preds[idx] == 1) & (lbls.data[idx] == 1)).cpu().sum()
            # TN    predict 和 label 
            TN += ((preds[idx] == 0) & (lbls.data[idx] == 0)).cpu().sum()
            # FN    predict 0 label 1
            FN += ((preds[idx] == 0) & (lbls.data[idx] == 1)).cpu().sum()
            # FP    predict 1 label 0
            FP += ((preds[idx] == 1) & (lbls.data[idx] == 0)).cpu().sum()

            epoch_loss = loss.item()
            epoch_acc = running_corrects.double() / len(idx)
            epoch_pre = TP.double() / (TP + FP)
            epoch_rec = TP.double() / (TP + FN)
            acc = (TP + TN).double() / (TP + TN + FP + FN)

            if epoch % print_freq == 0:
                print(f'{phase} Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f} Pre: {epoch_pre:.4f} Rec: {epoch_rec:.4f}')

            # deep copy the model
            epoch_f1 = (2 * epoch_pre * epoch_rec) / (epoch_pre + epoch_rec)  #
            # if phase == 'val' and epoch_f1 > best_f1:
            #     best_f1 = epoch_f1
            #     best_model_wts = copy.deepcopy(model.state_dict())
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss
                best_model_wts = copy.deepcopy(model.state_dict())
            if phase == 'val':
                loss_val.append(epoch_loss)
                # if epoch > cfg['early_stopping'] and epoch_loss > np.mean(loss_val[-cfg['early_stopping']+1:-1]):
                #     print("Early stopping...", loss_val[-cfg['early_stopping']+1:-1], epoch_loss)
                #     break
        else:
            continue
        break

    for name, parameters in model.named_parameters():
        parm1[name] = parameters.detach().cpu().numpy()
        # if epoch % print_freq == 0:
        #     print(f'Best val Acc: {best_acc:4f}')
        #     print('-' * 20)
    # load best model weights
    model.load_state_dict(best_model_wts)
    outputs = model(fts, support)
    outputs_pro = F.softmax(outputs)
    precision, recall, thresholds = precision_recall_curve(lbls[idx_test].cpu(), outputs_pro[idx_test, 1].cpu().detach())
    precision, recall, thresholds = remove_nan(precision, recall, thresholds)  # 
    auc = metrics.roc_auc_score(lbls[idx_test].cpu(), outputs_pro[idx_test, 1].cpu().detach())
    fscore = (2 * precision * recall) / (precision + recall)  # 
    acc = metrics.accuracy_score(lbls[idx_test].cpu(), outputs_pro[idx_test, 1].cpu().detach()>thresholds[np.argmax(fscore)])
    time_elapsed = time.time() - since
    print(f'\nTraining complete in {time_elapsed // 60:.0f}m {time_elapsed % 60:.0f}s')
    # print(f'Best val Acc: {best_acc:4f}')
    print(f'{phase} Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f} Pre: {epoch_pre:.4f} Rec: {epoch_rec:.4f}')

    return epoch_rec, epoch_pre, precision, recall, fscore, auc, acc



print('Configuration -> Start')
pp.pprint(cfg)
print('Configuration -> End')
global support
model = GCN
support = adj
model_ft = model(in_ch=fts.shape[1]*embedding_dim,
                n_class=n_class,
                n_hid=cfg['n_hid'],
                dropout=cfg['drop_out'],
                 embedding_dim=embedding_dim,
                 embedding_num=region_num)
# model_ft = torch.nn.DataParallel(model_ft, device_ids=[1, 2, 3, 4])
model_ft = model_ft.to(device)
for name, parameters in model_ft.named_parameters():
    print(name, ':', parameters.size())

optimizer = optim.Adam(model_ft.parameters(), lr=cfg['lr'],
                       weight_decay=cfg['weight_decay'])
# optimizer = optim.SGD(model_ft.parameters(), lr=0.01, weight_decay=cfg['weight_decay)
schedular = optim.lr_scheduler.MultiStepLR(optimizer,
                                           milestones=cfg['milestones'],
                                           gamma=cfg['gamma'])
# rec = []
# pre = []
# for i in range(0, 100):
#     criterion = torch.nn.CrossEntropyLoss(weight=torch.FloatTensor([i/100.0, (100-i)/100.0]).to(device))
#     temp = train_model(model_ft, criterion, optimizer, schedular, cfg['max_epoch'])
#     rec.append(temp[0])
#     pre.append(temp[1])
# plt.scatter(rec, pre)
# plt.savefig('plot1.png', format='png')

criterion = torch.nn.CrossEntropyLoss(weight=torch.FloatTensor([1, 1]).to(device))
temp = train_model(model_ft, criterion, optimizer, schedular, cfg['max_epoch'], cfg['print_freq'])
print(temp[2])
plt.plot(temp[2], temp[3])
# plt.xlim(0.1, 0.7)
plt.title('{} {}'.format(cfg['model'], cfg['dataset']))
plt.xlabel('precision')
plt.ylabel('recall')
plt.savefig('result/{} {} trade-off.png'.format(cfg['model'], cfg['dataset']), format='png')
print("F1:", temp[4].max())
index = np.argmax(temp[4])
print("precision:", temp[2][index]) #precision
print("recall:", temp[3][index]) #recall
print("auc:", temp[5]) #auc
print("acc:", temp[6]) #acc
print("BER:", temp[2][np.argmin(np.absolute(temp[2]-temp[3]))]) #BER

np.savetxt('result/{} {} pre.csv'.format(cfg['model'], cfg['dataset']), temp[2])
np.savetxt('result/{} {} rec.csv'.format(cfg['model'], cfg['dataset']), temp[3])

