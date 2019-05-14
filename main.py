import argparse

import pandas as pd
import torch
import torch.backends.cudnn as cudnn
import torchnet as tnt
from torch.nn import CrossEntropyLoss
from torch.nn import DataParallel
from torch.optim import Adam
from torch.optim.lr_scheduler import MultiStepLR
from torch.utils.data import DataLoader
from torchnet.logger import VisdomPlotLogger, VisdomLogger
from torchnlp.samplers import BucketBatchSampler

from model import Model
from utils import load_data, MarginLoss, collate_fn, FocalLoss


def reset_meters():
    meter_accuracy.reset()
    meter_loss.reset()
    meter_confusion.reset()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Train Text Classification')
    parser.add_argument('--data_type', default='imdb', type=str,
                        choices=['imdb', 'newsgroups', 'reuters', 'webkb', 'cade', 'dbpedia', 'agnews', 'yahoo',
                                 'sogou', 'yelp', 'amazon'], help='dataset type')
    parser.add_argument('--fine_grained', action='store_true', help='use fine grained class or not, it only works for '
                                                                    'reuters, yelp and amazon')
    parser.add_argument('--text_length', default=5000, type=int, help='the number of words about the text to load')
    parser.add_argument('--routing_type', default='k_means', type=str, choices=['k_means', 'dynamic'],
                        help='routing type')
    parser.add_argument('--loss_type', default='margin', type=str, choices=['margin', 'focal', 'cross'],
                        help='loss type')
    parser.add_argument('--num_iterations', default=3, type=int, help='routing iterations number')
    parser.add_argument('--batch_size', default=60, type=int, help='train batch size')
    parser.add_argument('--num_epochs', default=100, type=int, help='train epochs number')
    parser.add_argument('--num_steps', default=100, type=int, help='test steps number')
    parser.add_argument('--load_model_weight', default=None, type=str, help='saved model weight to load')

    opt = parser.parse_args()
    DATA_TYPE, FINE_GRAINED, TEXT_LENGTH = opt.data_type, opt.fine_grained, opt.text_length
    ROUTING_TYPE, LOSS_TYPE, NUM_ITERATIONS = opt.routing_type, opt.loss_type, opt.num_iterations
    BATCH_SIZE, NUM_EPOCHS, NUM_STEPS = opt.batch_size, opt.num_epochs, opt.num_steps
    MODEL_WEIGHT = opt.load_model_weight

    # prepare dataset
    vocab_size, num_class, train_dataset, test_dataset = load_data(DATA_TYPE, preprocessing=True,
                                                                   fine_grained=FINE_GRAINED, verbose=True,
                                                                   text_length=TEXT_LENGTH)
    print("[!] vocab_size: {}, num_class: {}".format(vocab_size, num_class))
    train_sampler = BucketBatchSampler(train_dataset, BATCH_SIZE, False, sort_key=lambda row: len(row['text']))
    train_iterator = DataLoader(train_dataset, batch_sampler=train_sampler, collate_fn=collate_fn)
    test_sampler = BucketBatchSampler(test_dataset, 200, False, sort_key=lambda row: len(row['text']))
    test_iterator = DataLoader(test_dataset, batch_sampler=test_sampler, collate_fn=collate_fn)

    model = Model(vocab_size, num_class=num_class, routing_type=ROUTING_TYPE, num_iterations=NUM_ITERATIONS)
    if MODEL_WEIGHT is not None:
        model.load_state_dict(torch.load('epochs/' + MODEL_WEIGHT))
    if LOSS_TYPE == 'margin':
        loss_criterion = MarginLoss()
    elif LOSS_TYPE == 'focal':
        loss_criterion = FocalLoss()
    else:
        loss_criterion = CrossEntropyLoss()
    if torch.cuda.is_available():
        model, loss_criterion = DataParallel(model.to('cuda')), loss_criterion.to('cuda')
        cudnn.benchmark = True

    optimizer = Adam(model.parameters(), weight_decay=5e-4)
    scheduler = MultiStepLR(optimizer, milestones=[50, 70])
    print("# trainable parameters:", sum(param.numel() for param in model.parameters()))
    # record statistics
    results = {'train_loss': [], 'train_accuracy': [], 'test_loss': [], 'test_accuracy': []}
    # record current best test accuracy
    best_acc = 0
    meter_loss = tnt.meter.AverageValueMeter()
    meter_accuracy = tnt.meter.ClassErrorMeter(accuracy=True)
    meter_confusion = tnt.meter.ConfusionMeter(num_class, normalized=True)

    # config the visdom figures
    if FINE_GRAINED and DATA_TYPE in ['reuters', 'yelp', 'amazon']:
        env_name = DATA_TYPE + '_fine_grained'
    else:
        env_name = DATA_TYPE
    train_loss_logger = VisdomPlotLogger('line', env=env_name, opts={'title': 'Train Loss'})
    train_accuracy_logger = VisdomPlotLogger('line', env=env_name, opts={'title': 'Train Accuracy'})
    train_confusion_logger = VisdomLogger('heatmap', env=env_name, opts={'title': 'Train Confusion Matrix'})
    test_loss_logger = VisdomPlotLogger('line', env=env_name, opts={'title': 'Test Loss'})
    test_accuracy_logger = VisdomPlotLogger('line', env=env_name, opts={'title': 'Test Accuracy'})
    test_confusion_logger = VisdomLogger('heatmap', env=env_name, opts={'title': 'Test Confusion Matrix'})

    current_step = 0
    for epoch in range(1, NUM_EPOCHS + 1):
        for data, target in train_iterator:
            current_step += 1
            if LOSS_TYPE == 'margin':
                label = torch.eye(num_class).index_select(dim=0, index=target)
            else:
                label = target
            if torch.cuda.is_available():
                data, label = data.to('cuda'), label.to('cuda')
            # train model
            model.train()
            optimizer.zero_grad()
            classes = model(data)
            loss = loss_criterion(classes, label)
            loss.backward()
            optimizer.step()
            # save the metrics
            meter_loss.add(loss.detach().cpu().item())
            meter_accuracy.add(classes.detach().cpu(), target)
            meter_confusion.add(classes.detach().cpu(), target)

            if current_step % NUM_STEPS == 0:
                # print the information about train
                train_loss_logger.log(current_step // NUM_STEPS, meter_loss.value()[0])
                train_accuracy_logger.log(current_step // NUM_STEPS, meter_accuracy.value()[0])
                train_confusion_logger.log(meter_confusion.value())
                results['train_loss'].append(meter_loss.value()[0])
                results['train_accuracy'].append(meter_accuracy.value()[0])
                print('[Step %d] Training Loss: %.4f Accuracy: %.2f%%' % (
                    current_step // NUM_STEPS, meter_loss.value()[0], meter_accuracy.value()[0]))
                reset_meters()

                # test model periodically
                model.eval()
                with torch.no_grad():
                    for data, target in test_iterator:
                        if LOSS_TYPE == 'margin':
                            label = torch.eye(num_class).index_select(dim=0, index=target)
                        else:
                            label = target
                        if torch.cuda.is_available():
                            data, label = data.to('cuda'), label.to('cuda')
                        classes = model(data)
                        loss = loss_criterion(classes, label)
                        # save the metrics
                        meter_loss.add(loss.detach().cpu().item())
                        meter_accuracy.add(classes.detach().cpu(), target)
                        meter_confusion.add(classes.detach().cpu(), target)
                    # print the information about test
                    test_loss_logger.log(current_step // NUM_STEPS, meter_loss.value()[0])
                    test_accuracy_logger.log(current_step // NUM_STEPS, meter_accuracy.value()[0])
                    test_confusion_logger.log(meter_confusion.value())
                    results['test_loss'].append(meter_loss.value()[0])
                    results['test_accuracy'].append(meter_accuracy.value()[0])

                # save best model
                if meter_accuracy.value()[0] > best_acc:
                    best_acc = meter_accuracy.value()[0]
                    if FINE_GRAINED and DATA_TYPE in ['reuters', 'yelp', 'amazon']:
                        if torch.cuda.is_available():
                            torch.save(model.module.state_dict(), 'epochs/%s.pth' % (DATA_TYPE + '_fine_grained'))
                        else:
                            torch.save(model.state_dict(), 'epochs/%s.pth' % (DATA_TYPE + '_fine_grained'))
                    else:
                        if torch.cuda.is_available():
                            torch.save(model.module.state_dict(), 'epochs/%s.pth' % DATA_TYPE)
                        else:
                            torch.save(model.state_dict(), 'epochs/%s.pth' % DATA_TYPE)
                print('[Step %d] Testing Loss: %.4f Accuracy: %.2f%% Best Accuracy: %.2f%%' % (
                    current_step // NUM_STEPS, meter_loss.value()[0], meter_accuracy.value()[0], best_acc))
                reset_meters()

                # save statistics
                out_path = 'statistics/'
                data_frame = pd.DataFrame(
                    data={'train_loss': results['train_loss'], 'train_accuracy': results['train_accuracy'],
                          'test_loss': results['test_loss'], 'test_accuracy': results['test_accuracy']},
                    index=range(1, current_step // NUM_STEPS + 1))
                if FINE_GRAINED and DATA_TYPE in ['reuters', 'yelp', 'amazon']:
                    data_frame.to_csv(out_path + DATA_TYPE + '_fine_grained' + '_results.csv', index_label='step')
                else:
                    data_frame.to_csv(out_path + DATA_TYPE + '_results.csv', index_label='step')
        scheduler.step(epoch - 1)
