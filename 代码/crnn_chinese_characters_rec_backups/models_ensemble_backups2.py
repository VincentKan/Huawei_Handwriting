from __future__ import print_function
import argparse
import random
import torch
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
from torch.autograd import Variable
import numpy as np
from warpctc_pytorch import CTCLoss
import os
import utils
import dataset
import models.crnn_backups as crnn
import models.resnet_crnn as resnet_crnn
import models.crnn_expand_dim as expand_crnn
import re
import models_ensemble_params as params
import logging
import os
import time
import sys
from tensorboardX import SummaryWriter
from tqdm import tqdm
import mmcv
import gc
import torchvision.transforms as transforms

parser = argparse.ArgumentParser()
parser.add_argument('--trainroot', required=True, help='path to dataset')
parser.add_argument('--valroot', required=True, help='path to dataset')
parser.add_argument('--cuda', type=bool, default=True, help='enables cuda')
parser.add_argument('--GPU_ID', type=str, default=None, help='GPU_ID')
opt = parser.parse_args()
print(opt)
def get_log_dir():
    run_id = params.name+f'_lr_{params.lr:.7f}_batchSize_{params.batchSize:d}_time_%s_'%time.strftime('%m%d%H%M%S')+'/'
    log_dir = os.path.join(params.log_dir, run_id)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    return log_dir

def get_logger(log_dir, name, log_filename='info.log', level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Add file handler and stdout handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(os.path.join(log_dir, log_filename))
    file_handler.setFormatter(formatter)
    # Add console handler.
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    # Add google cloud log handler
    logger.info('Log directory: %s', log_dir)
    return logger
    
# custom weights initialization called on crnn
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


def val(net, net2, net3, _dataset, _dataset2, epoch, step, criterion, max_iter=100):
    logger.info('Start val')
    # for p in crnn.parameters():
    #     p.requires_grad = False
    net.eval()
    net2.eval()
    net3.eval()
    net2.cuda()
    data_loader = torch.utils.data.DataLoader(
        _dataset, shuffle=False, batch_size=params.batchSize, num_workers=int(params.workers), collate_fn=dataset.alignCollate(imgH=params.imgH, imgW=params.imgW, keep_ratio=params.keep_ratio))
    data_loader2 = torch.utils.data.DataLoader(
        _dataset2, shuffle=False, batch_size=params.batchSize, num_workers=int(params.workers), collate_fn=dataset.alignCollate(imgH=params.resnet_imgH, imgW=params.imgW, keep_ratio=params.keep_ratio, rgb = True))
    val_iter = iter(data_loader)
    val_iter2 = iter(data_loader2)
    i = 0
    n_correct = 0
    loss_avg = utils.averager()
    max_iter = len(data_loader)
    record_dir = log_dir + 'epoch_%d_step_%d_data.txt'%(epoch, step)
    record_dir1 = log_dir + 'epoch_%d_step_%d_data1.txt'%(epoch, step)
    record_dir2 = log_dir + 'epoch_%d_step_%d_data2.txt'%(epoch, step)
    r = 1
    f = open(record_dir, "a")
    f1 = open(record_dir1, "a")
    f2 = open(record_dir2, "a")
    num_label, num_pred = params.total_num, 0

    start = time.time()
    for i in range(max_iter):
        data = val_iter.next()
        data2 = val_iter2.next()
        if i<6000:
            pass #continue
        i += 1
        cpu_images, cpu_texts = data
        resnet_images, _ = data2
        batch_size = cpu_images.size(0)
        utils.loadData(image, cpu_images)
        utils.loadData(image2, resnet_images)
        t, l = converter.encode(cpu_texts)
        utils.loadData(text, t)
        utils.loadData(length, l)

        with torch.no_grad():
            n1img=net(image)
            n2img=net2(image2)
            n3img=net3(image)
        preds_size = Variable(torch.IntTensor([n1img.size(0)] * batch_size))
        
        _, n1 = n1img.max(2)
        _, n2 = n2img.max(2)
        _, n3 = n3img.max(2)
        ind = torch.arange(batch_size)
        _ind  = torch.arange(batch_size)
        n1_index = n1.transpose(1, 0).data
        n2_index = n2.transpose(1, 0).data
        n3_index = n3.transpose(1, 0).data
        ind = ind[torch.sum(n1_index!=0, 1)==torch.sum(n2_index!=0, 1)]
        _ind = _ind[(torch.sum(n1_index!=0, 1)==torch.sum(n2_index!=0, 1)) * (torch.sum(n3_index!=0, 1)==torch.sum(n2_index!=0, 1))]
        for i in ind:
            ind1 = np.arange(n1img.shape[0])
            ind2 = np.arange(n2img.shape[0])
            ind1 = ind1[(n1_index[int(i), :].cpu().numpy().astype(bool)!=0)]
            ind2 = ind2[(n2_index[int(i), :].cpu().numpy().astype(bool)!=0)]
            #n1img[ind1, int(i), :] = (n1img[ind1, int(i), :] + n2img[ind2, int(i), :])/2
           
            if torch.sum(int(i)==_ind)>0:
                ind3 = np.arange(n1img.shape[0])
                ind3 = ind3[(n3_index[int(i), :].cpu().numpy().astype(bool)!=0)]
                n1img[ind1, int(i), :] = (n1img[ind1, int(i), :] + n2img[ind2, int(i), :] + n3img[ind3, int(i), :])/3  #+ n3img[ind3, int(i), :]
            else:    
                n1img[ind1, int(i), :] = (n1img[ind1, int(i), :] + n2img[ind2, int(i), :])/2
           
        preds = n1img    
        cost = criterion(preds, text, preds_size, length) / batch_size
        loss_avg.add(cost)
        _, preds = preds.max(2)
        preds = preds.transpose(1, 0).contiguous().view(-1)
        sim_preds = converter.decode(preds.data, preds_size.data, raw=False)
        if not isinstance(sim_preds, list):
            sim_preds = [sim_preds]
        
        for i, pred in enumerate(sim_preds):
            f.write(str(r).zfill(6)+".jpg "+pred+"\n")
            r += 1
        list_1 = []
        for i in cpu_texts:
            string = i.decode('utf-8', 'strict')
            list_1.append(string)     
        for pred, target in zip(sim_preds, list_1):
            if pred == target:
                n_correct += 1
        num_pred += len(sim_preds)

    print("")
    f.close()

    
    raw_preds = converter.decode(preds.data, preds_size.data, raw=True)[:params.n_test_disp]
    for raw_pred, pred, gt in zip(raw_preds, sim_preds, list_1):
        logger.info('%-20s => %-20s, gt: %-20s' % (raw_pred, pred, gt))

    logger.info('correct_num: %d'%(n_correct))
    logger.info('Total_num: %d'%(max_iter*params.batchSize))
    accuracy = float(n_correct) / num_pred
    recall = float(n_correct) / num_label
    logger.info('Test loss: %f, accuray: %f, recall: %f, F1 score: %f, Cost : %.4fs per img'
                % (loss_avg.val(), accuracy, recall, 2*accuracy*recall/(accuracy+recall+1e-2), (time.time()-start)/max_iter))



def trainBatch(net, criterion, optimizer, train_iter):
    data = train_iter.next()
    cpu_images, cpu_texts = data
    batch_size = cpu_images.size(0)
    utils.loadData(image, cpu_images)
    t, l = converter.encode(cpu_texts)
    utils.loadData(text, t)
    utils.loadData(length, l)
    preds = crnn1(image)
    preds_size = Variable(torch.IntTensor([preds.size(0)] * batch_size))
    cost = criterion(preds, text, preds_size, length) / batch_size
    crnn1.zero_grad()
    cost.backward()
    optimizer.step()
    return cost
def training():
    for total_steps in range(params.niter):
        train_iter = iter(train_loader)
        i = 0
        logger.info('length of train_data: %d'%(len(train_loader)))
        while i < len(train_loader):
         
            for p in crnn1.parameters():
                p.requires_grad = True
            for p in crnn2.parameters():
                p.requires_grad = True 
            for p in crnn3.parameters():
                p.requires_grad = True      
            crnn1.train()
            crnn2.train()
            crnn3.train()
            val(crnn1, crnn2, crnn3, test_dataset, test_dataset2, total_steps, i, criterion)   
            return
            cost = trainBatch(crnn1, criterion, optimizer, train_iter)
            loss_avg.add(cost)
            i += 1
            if i % params.displayInterval == 0:
                logger.info('[%d/%d][%d/%d] Loss: %f' %
                      (total_steps, params.niter, i, len(train_loader), loss_avg.val()))
                loss_avg.reset()
        val(crnn1, crnn2, test_dataset, total_steps, i, criterion)        
        if (total_steps+1) % params.saveInterval == 0:
            string = "model save to {0}crnn_Rec_done_epoch_{1}.pth".format(log_dir, total_steps)
            logger.info(string)
            torch.save(crnn1.state_dict(), '{0}crnn_Rec_done_epoch_{1}.pth'.format(log_dir, total_steps))
        

if __name__ == '__main__':

    manualSeed = random.randint(1, 10000)  # fix seed
    random.seed(manualSeed)
    np.random.seed(manualSeed)
    torch.manual_seed(manualSeed)
    cudnn.benchmark = True
    log_dir = get_log_dir()
    logger = get_logger(log_dir, params.name, params.name+'_info.log')
    logger.info(opt)
    # store model path
    if not os.path.exists('./expr'):
        os.mkdir('./expr')

    # read train set
    train_dataset = dataset.lmdbDataset(root=opt.trainroot, rand_hcrop=params.with_crop)
    assert train_dataset
    if params.random_sample:
        sampler = dataset.randomSequentialSampler(train_dataset, params.batchSize)
    else:
        sampler = None
    os.environ["CUDA_VISIBLE_DEVICES"] = opt.GPU_ID
    # images will be resize to 32*160
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=params.batchSize,
        shuffle=False, sampler=sampler,
        num_workers=int(params.workers),
        collate_fn=dataset.alignCollate(imgH=params.imgH, imgW=params.imgW, keep_ratio=params.keep_ratio))
    # read test set
    # images will be resize to 32*160
    test_dataset = dataset.lmdbDataset(
        root=opt.valroot)
    test_dataset2 = dataset.lmdbDataset(
        root=opt.valroot, rgb=True)

    nclass = len(params.alphabet) + 1
    nc = 1

    converter = utils.strLabelConverter(params.alphabet)
    criterion = CTCLoss()

    # cnn and rnn
    image = torch.FloatTensor(params.batchSize, 1, params.imgH, params.imgH)
    image2 = torch.FloatTensor(params.batchSize, 3, params.resnet_imgH, params.resnet_imgH)
    text = torch.IntTensor(params.batchSize * 5)
    length = torch.IntTensor(params.batchSize)

    crnn1 = crnn.CRNN(params.imgH, nc, nclass, params.nh)
    crnn2 = resnet_crnn.ResNetCRNN(params.resnet_imgH, nc, nclass, params.nh, resnet_type=params.resnet_type, feat_size=params.feat_size ) 
    crnn3 = crnn.CRNN(params.imgH, nc, nclass, params.expand_nh)
    if opt.cuda:
        crnn1.cuda()
        crnn2.cuda()
        crnn3.cuda()
        image = image.cuda()
        image2 = image2.cuda()
        criterion = criterion.cuda()
    crnn1.apply(weights_init)
    crnn2.apply(weights_init)
    crnn3.apply(weights_init)
    if params.crnn1 != '':
        logger.info('loading pretrained model from %s' % params.crnn1)
        crnn1.load_state_dict(torch.load(params.crnn1))
    if params.crnn2 != '':
        logger.info('loading pretrained model from %s' % params.crnn2)
        d = torch.load(params.crnn2)
        d1 = {}
        for key in d.keys():
            d1[key[7:]] = d[key]
        crnn2.load_state_dict(d1)
    if params.crnn3 != '':
        logger.info('loading pretrained model from %s' % params.crnn3)
        crnn3.load_state_dict(torch.load(params.crnn3))
    image = Variable(image)
    text = Variable(text)
    length = Variable(length)

    # loss averager
    loss_avg = utils.averager()

    # setup optimizer
    if params.adam:
        optimizer = optim.Adam(crnn1.parameters(), lr=params.lr,
                               betas=(params.beta1, 0.999))
    elif params.adadelta:
        optimizer = optim.Adadelta(crnn1.parameters(), lr=params.lr)
    else:
        optimizer = optim.RMSprop(crnn1.parameters(), lr=params.lr)

    training()
