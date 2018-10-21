import os
import sys
import time
import shutil
import torch
import argparse
import importlib
import traceback
import numpy as np

import torch.nn as nn
from torch import optim
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter

from utils.generic_utils import (
    synthesis, remove_experiment_folder, create_experiment_folder,
    save_checkpoint, save_best_model, load_config, lr_decay, count_parameters,
    check_update, get_commit_hash, sequence_mask, AnnealLR)
from utils.visual import plot_alignment, plot_spectrogram
from models.tacotron2 import Tacotron2
from layers.losses import L1LossMasked
from utils.audio import AudioProcessor

torch.manual_seed(1)
use_cuda = torch.cuda.is_available()


def train(model, criterion, criterion_st, data_loader, optimizer, optimizer_st,
          scheduler, ap, epoch):
    model = model.train()
    epoch_time = 0
    avg_linear_loss = 0
    avg_mel_loss = 0
    avg_stop_loss = 0
    avg_step_time = 0
    print(" | > Epoch {}/{}".format(epoch, c.epochs), flush=True)
    n_priority_freq = int(3000 / (c.audio['sample_rate'] * 0.5) * c.audio['num_freq'])
    batch_n_iter = int(len(data_loader.dataset) / c.batch_size)
    for num_iter, data in enumerate(data_loader):
        start_time = time.time()

        # setup input data
        text_input = data[0]
        text_lengths = data[1]
        mel_input = data[2]
        mel_lengths = data[3]
        stop_targets = data[4]

        # set stop targets view, we predict a single stop token per r frames prediction
        stop_targets = stop_targets.view(text_input.shape[0],
                                         stop_targets.size(1) // c.r, -1)
        stop_targets = (stop_targets.sum(2) > 0.0).unsqueeze(2).float()

        current_step = num_iter + args.restore_step + \
            epoch * len(data_loader) + 1

        # setup lr
        scheduler.step()
        optimizer.zero_grad()
        optimizer_st.zero_grad()

        # dispatch data to GPU
        if use_cuda:
            text_input = text_input.cuda()
            text_lengths = text_lengths.cuda()
            mel_input = mel_input.cuda()
            mel_lengths = mel_lengths.cuda()
            stop_targets = stop_targets.cuda()

        # compute mask for padding
        mask = sequence_mask(text_lengths)

        # forward pass
        mel_output, alignments, stop_tokens = torch.nn.parallel.data_parallel(
            model, (text_input, mel_input, mask))

        # loss computation
        stop_loss = criterion_st(stop_tokens, stop_targets)
        mel_loss = criterion(mel_output, mel_input, mel_lengths)

        # backpass and check the grad norm for spec losses
        mel_loss.backward(retain_graph=True)
        for group in optimizer.param_groups:
            for param in group['params']:
                param.data = param.data.add(-c.wd * group['lr'], param.data)
        grad_norm, skip_flag = check_update(model, 1)
        if skip_flag:
            optimizer.zero_grad()
            print(" | > Iteration skipped!!", flush=True)
            continue
        optimizer.step()

        # backpass and check the grad norm for stop loss
        stop_loss.backward()
        for group in optimizer_st.param_groups:
            for param in group['params']:
                param.data = param.data.add(-c.wd * group['lr'], param.data)
        grad_norm_st, skip_flag = check_update(model.decoder.stopnet, 0.5)
        if skip_flag:
            optimizer_st.zero_grad()
            print(" | | > Iteration skipped fro stopnet!!")
            continue
        optimizer_st.step()

        step_time = time.time() - start_time
        epoch_time += step_time

        if current_step % c.print_step == 0:
            print(
                " | | > Step:{}/{}  GlobalStep:{} "
                "MelLoss:{:.5f}  StopLoss:{:.5f}  GradNorm:{:.5f}  "
                "GradNormST:{:.5f}  StepTime:{:.2f}".format(
                    num_iter, batch_n_iter, current_step,
                    mel_loss.item(), stop_loss.item(),
                    grad_norm, grad_norm_st, step_time),
                flush=True)

        avg_mel_loss += mel_loss.item()
        avg_stop_loss += stop_loss.item()
        avg_step_time += step_time

        # Plot Training Iter Stats
        tb.add_scalar('TrainIterLoss/MelLoss', mel_loss.item(), current_step)
        tb.add_scalar('Params/LearningRate', optimizer.param_groups[0]['lr'],
                      current_step)
        tb.add_scalar('Params/GradNorm', grad_norm, current_step)
        tb.add_scalar('Params/GradNormSt', grad_norm_st, current_step)
        tb.add_scalar('Time/StepTime', step_time, current_step)

        if current_step % c.save_step == 0:
            if c.checkpoint:
                # save model
                save_checkpoint(model, optimizer, optimizer_st,
                                mel_loss.item(), OUT_PATH, current_step,
                                epoch)

            # Diagnostic visualizations
            const_spec = mel_output[0].data.cpu().numpy()
            gt_spec = mel_input[0].data.cpu().numpy()

            const_spec = plot_spectrogram(const_spec, ap)
            gt_spec = plot_spectrogram(gt_spec, ap)
            tb.add_figure('Visual/Reconstruction', const_spec, current_step)
            tb.add_figure('Visual/GroundTruth', gt_spec, current_step)

            align_img = alignments[0].data.cpu().numpy()
            align_img = plot_alignment(align_img)
            tb.add_figure('Visual/Alignment', align_img, current_step)

    avg_mel_loss /= (num_iter + 1)
    avg_stop_loss /= (num_iter + 1)
    avg_step_time /= (num_iter + 1)

    # print epoch stats
    print(
        " | | > EPOCH END -- GlobalStep:{} "
        "AvgMelLoss:{:.5f}  "
        "AvgStopLoss:{:.5f}  EpochTime:{:.2f}  "
        "AvgStepTime:{:.2f}".format(current_step,
                                    avg_mel_loss,
                                    avg_stop_loss, epoch_time, avg_step_time),
        flush=True)

    # Plot Training Epoch Stats
    tb.add_scalar('TrainEpochLoss/MelLoss', avg_mel_loss, current_step)
    tb.add_scalar('TrainEpochLoss/StopLoss', avg_stop_loss, current_step)
    tb.add_scalar('Time/EpochTime', epoch_time, epoch)
    epoch_time = 0
    return avg_mel_loss, current_step


def evaluate(model, criterion, criterion_st, data_loader, ap, current_step):
    model = model.eval()
    epoch_time = 0
    avg_linear_loss = 0
    avg_mel_loss = 0
    avg_stop_loss = 0
    print(" | > Validation")
    test_sentences = [
        "It took me quite a long time to develop a voice, and now that I have it I'm not going to be silent.",
        "Be a voice, not an echo.",
        "I'm sorry Dave. I'm afraid I can't do that.",
        "This cake is great. It's so delicious and moist."
    ]
    n_priority_freq = int(3000 / (c.audio['sample_rate'] * 0.5) * c.audio['num_freq'])
    with torch.no_grad():
        if data_loader is not None:
            for num_iter, data in enumerate(data_loader):
                start_time = time.time()

                # setup input data
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_targets = data[4]

                # set stop targets view, we predict a single stop token per r frames prediction
                stop_targets = stop_targets.view(text_input.shape[0],
                                                 stop_targets.size(1) // c.r,
                                                 -1)
                stop_targets = (stop_targets.sum(2) > 0.0).unsqueeze(2).float()

                # dispatch data to GPU
                if use_cuda:
                    text_input = text_input.cuda()
                    mel_input = mel_input.cuda()
                    mel_lengths = mel_lengths.cuda()
                    stop_targets = stop_targets.cuda()

                # forward pass
                mel_output, alignments, stop_tokens =\
                    model.forward(text_input, mel_input)

                # loss computation
                stop_loss = criterion_st(stop_tokens, stop_targets)
                mel_loss = criterion(mel_output, mel_input, mel_lengths)

                step_time = time.time() - start_time
                epoch_time += step_time

                if num_iter % c.print_step == 0:
                    print(
                        " | | > MelLoss:{:.5f}  "
                        "StopLoss: {:.5f}  ".format(mel_loss.item(),
                                                    stop_loss.item()),
                        flush=True)

                avg_mel_loss += mel_loss.item()
                avg_stop_loss += stop_loss.item()

            # Diagnostic visualizations
            idx = np.random.randint(mel_input.shape[0])
            const_spec = mel_output[idx].data.cpu().numpy()
            gt_spec = mel_input[idx].data.cpu().numpy()
            align_img = alignments[idx].data.cpu().numpy()

            const_spec = plot_spectrogram(const_spec, ap)
            gt_spec = plot_spectrogram(gt_spec, ap)
            align_img = plot_alignment(align_img)

            tb.add_figure('ValVisual/Reconstruction', const_spec, current_step)
            tb.add_figure('ValVisual/GroundTruth', gt_spec, current_step)
            tb.add_figure('ValVisual/ValidationAlignment', align_img,
                          current_step)

            # compute average losses
            avg_mel_loss /= (num_iter + 1)
            avg_stop_loss /= (num_iter + 1)

            # Plot Learning Stats
            tb.add_scalar('ValEpochLoss/MelLoss', avg_mel_loss, current_step)
            tb.add_scalar('ValEpochLoss/Stop_loss', avg_stop_loss,
                          current_step)

    # test sentences
    ap.griffin_lim_iters = 60
    for idx, test_sentence in enumerate(test_sentences):
        try:
            mel_spec, alignments = synthesis(model, ap, test_sentence,
                                                     use_cuda, c.text_cleaner)
            wav_name = 'TestSentences/{}'.format(idx)
            align_img = alignments[0].data.cpu().numpy()
            mel_spec = plot_spectrogram(mel_spec, ap)
            align_img = plot_alignment(align_img)
            tb.add_figure('TestSentences/{}_MelSpectrogram'.format(idx),
                          mel_spec, current_step)
            tb.add_figure('TestSentences/{}_Alignment'.format(idx), align_img,
                          current_step)
        except:
            print(" !! Error as creating Test Sentence -", idx)
            pass
    return avg_mel_loss


def main(args):
    preprocessor = importlib.import_module('datasets.preprocess')
    preprocessor = getattr(preprocessor, c.dataset.lower())
    MyDataset = importlib.import_module('datasets.'+c.data_loader)
    MyDataset = getattr(MyDataset, "MyDataset")
    audio = importlib.import_module('utils.' + c.audio['audio_processor'])
    AudioProcessor = getattr(audio, 'AudioProcessor')

    ap = AudioProcessor(**c.audio)

    # Setup the dataset
    train_dataset = MyDataset(
        c.data_path,
        c.meta_file_train,
        c.r,
        c.text_cleaner,
        preprocessor=preprocessor,
        ap=ap,
        batch_group_size=8*c.batch_size,
        min_seq_len=c.min_seq_len)

    train_loader = DataLoader(
        train_dataset,
        batch_size=c.batch_size,
        shuffle=False,
        collate_fn=train_dataset.collate_fn,
        drop_last=False,
        num_workers=c.num_loader_workers,
        pin_memory=True)

    if c.run_eval:
        val_dataset = MyDataset(
            c.data_path, c.meta_file_val, c.r, c.text_cleaner, preprocessor=preprocessor, ap=ap, batch_group_size=0)

        val_loader = DataLoader(
            val_dataset,
            batch_size=c.eval_batch_size,
            shuffle=False,
            collate_fn=val_dataset.collate_fn,
            drop_last=False,
            num_workers=4,
            pin_memory=True)
    else:
        val_loader = None

    model = Tacotron2(ap.num_mels, c.r, **c.model)
    print(" | > Num output units : {}".format(ap.num_freq), flush=True)

    optimizer = optim.Adam(model.parameters(), lr=c.lr, weight_decay=0)
    optimizer_st = optim.Adam(
        model.decoder.stopnet.parameters(), lr=c.lr, weight_decay=0)

    criterion = L1LossMasked()
    criterion_st = nn.BCELoss()

    if args.restore_path:
        checkpoint = torch.load(args.restore_path)
        model_dict = model.state_dict()
        # 1. filter out unnecessary keys
        checkpoint['model'] = {k: v for k, v in checkpoint['model'].items() if k in model_dict}
        # 2. overwrite entries in the existing state dict
        model_dict.update(checkpoint['model']) 
        # 3. load the new state dict
        model.load_state_dict(model_dict)
        if use_cuda:
            model = model.cuda()
            criterion.cuda()
            criterion_st.cuda()
        for state in optimizer.state.values():
            for k, v in state.items():
                if torch.is_tensor(v):
                    state[k] = v.cuda()
        optimizer.load_state_dict(checkpoint['optimizer'])
        print(
            " > Model restored from step %d" % checkpoint['step'], flush=True)
        start_epoch = checkpoint['step'] // len(train_loader)
        best_loss = checkpoint['linear_loss']
        args.restore_step = checkpoint['step']
    else:
        args.restore_step = 0
        print("\n > Starting a new training", flush=True)
        if use_cuda:
            model = model.cuda()
            criterion.cuda()
            criterion_st.cuda()

    scheduler = AnnealLR(optimizer, warmup_steps=c.warmup_steps, last_epoch=args.restore_step - 1)
    num_params = count_parameters(model)
    print(" | > Model has {} parameters".format(num_params), flush=True)

    if not os.path.exists(CHECKPOINT_PATH):
        os.mkdir(CHECKPOINT_PATH)

    if 'best_loss' not in locals():
        best_loss = float('inf')

    for epoch in range(0, c.epochs):
        train_loss, current_step = train(model, criterion, criterion_st,
                                         train_loader, optimizer, optimizer_st,
                                         scheduler, ap, epoch)
        val_loss = evaluate(model, criterion, criterion_st, val_loader, ap,
                            current_step)
        print(
            " | > Train Loss: {:.5f}   Validation Loss: {:.5f}".format(
                train_loss, val_loss),
            flush=True)
        best_loss = save_best_model(model, optimizer, train_loss, best_loss,
                                    OUT_PATH, current_step, epoch)
         # shuffle batch groups
        train_loader.dataset.sort_items()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--restore_path',
        type=str,
        help='Folder path to checkpoints',
        default=0)
    parser.add_argument(
        '--config_path',
        type=str,
        help='path to config file for training',
    )
    parser.add_argument(
        '--debug',
        type=bool,
        default=False,
        help='do not ask for git has before run.')
    parser.add_argument(
        '--data_path',
        type=str,
        help='dataset path.',
        default=''
    )
    args = parser.parse_args()

    # setup output paths and read configs
    c = load_config(args.config_path)
    _ = os.path.dirname(os.path.realpath(__file__))
    OUT_PATH = os.path.join(_, c.output_path)
    OUT_PATH = create_experiment_folder(OUT_PATH, c.model_name, args.debug)
    CHECKPOINT_PATH = os.path.join(OUT_PATH, 'checkpoints')
    AUDIO_PATH = os.path.join(OUT_PATH, 'test_audios')
    os.makedirs(AUDIO_PATH, exist_ok=True)
    shutil.copyfile(args.config_path, os.path.join(OUT_PATH, 'config.json'))

    if args.data_path != '':
        c.data_path = args.data_path

    # setup tensorboard
    LOG_DIR = OUT_PATH
    tb = SummaryWriter(LOG_DIR)

    try:
        main(args)
    except KeyboardInterrupt:
        remove_experiment_folder(OUT_PATH)
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
    except Exception:
        remove_experiment_folder(OUT_PATH)
        traceback.print_exc()
        sys.exit(1)
