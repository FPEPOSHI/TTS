import os
import unittest
import numpy as np

from torch.utils.data import DataLoader
from TTS.utils.generic_utils import load_config
from TTS.utils.audio import AudioProcessor
from TTS.datasets import TTSDataset, TTSDatasetCached, TTSDatasetMemory
from TTS.datasets.preprocess import ljspeech, tts_cache

file_path = os.path.dirname(os.path.realpath(__file__))
OUTPATH = os.path.join(file_path, "outputs")
c = load_config(os.path.join(file_path, 'test_config.json'))
ok_ljspeech = os.path.exists(c.data_path)


class TestTTSDataset(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestTTSDataset, self).__init__(*args, **kwargs)
        self.max_loader_iter = 4
        self.ap = AudioProcessor(**c.audio)

    def test_loader(self):
        if ok_ljspeech:
            dataset = TTSDataset.MyDataset(
                c.data_path,
                'metadata.csv',
                c.r,
                c.text_cleaner,
                preprocessor = ljspeech,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # TODO: more assertion here
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']

    def test_batch_group_shuffle(self):
        if ok_ljspeech:
            dataset = TTSDataset.MyDataset(
                c.data_path,
                'metadata.csv',
                c.r,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                batch_group_size=16,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            frames = dataset.items
            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # TODO: more assertion here
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']
            dataloader.dataset.sort_items()
            assert frames[0] != dataloader.dataset.items[0]


    def test_padding(self):
        if ok_ljspeech:
            dataset = TTSDataset.MyDataset(
                c.data_path,
               'metadata.csv',
                1,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            # Test for batch size 1
            dataloader = DataLoader(
                dataset,
                batch_size=1,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                # linear_input = data[2]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                # check the last time step to be zero padded
                assert mel_input[0, -1].sum() == 0
                assert mel_input[0, -2].sum() != 0
                assert stop_target[0, -1] == 1
                assert stop_target[0, -2] == 0
                assert stop_target.sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[0] == mel_input[0].shape[0]

            # Test for batch size 2
            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=False,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                if mel_lengths[0] > mel_lengths[1]:
                    idx = 0
                else:
                    idx = 1

                # check the first item in the batch
                assert mel_input[idx, -1].sum() == 0
                assert mel_input[idx, -2].sum() != 0, mel_input
                assert stop_target[idx, -1] == 1
                assert stop_target[idx, -2] == 0
                assert stop_target[idx].sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[idx] == mel_input[idx].shape[0]

                # check the second itme in the batch
                assert mel_input[1 - idx, -1].sum() == 0
                assert stop_target[1 - idx, -1] == 1
                assert len(mel_lengths.shape) == 1

                # check batch conditions
                assert (mel_input * stop_target.unsqueeze(2)).sum() == 0


class TestTTSDatasetCached(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestTTSDatasetCached, self).__init__(*args, **kwargs)
        self.max_loader_iter = 4
        self.ap = AudioProcessor(**c.audio)

    def test_loader(self):
        if ok_ljspeech:
            dataset = TTSDatasetCached.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                c.r,
                c.text_cleaner,
                preprocessor = tts_cache,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # TODO: more assertion here
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']

    def test_batch_group_shuffle(self):
        if ok_ljspeech:
            dataset = TTSDatasetCached.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                c.r,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                batch_group_size=16,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            frames = dataset.items
            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # TODO: more assertion here
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']
            dataloader.dataset.sort_items()
            assert frames[0] != dataloader.dataset.items[0]


    def test_padding(self):
        if ok_ljspeech:
            dataset = TTSDatasetCached.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                1,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            # Test for batch size 1
            dataloader = DataLoader(
                dataset,
                batch_size=1,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                # linear_input = data[2]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                # check the last time step to be zero padded
                assert mel_input[0, -1].sum() == 0
                assert mel_input[0, -2].sum() != 0
                assert stop_target[0, -1] == 1
                assert stop_target[0, -2] == 0
                assert stop_target.sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[0] == mel_input[0].shape[0]

            # Test for batch size 2
            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=False,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                if mel_lengths[0] > mel_lengths[1]:
                    idx = 0
                else:
                    idx = 1

                # check the first item in the batch
                assert mel_input[idx, -1].sum() == 0
                assert mel_input[idx, -2].sum() != 0, mel_input
                assert stop_target[idx, -1] == 1
                assert stop_target[idx, -2] == 0
                assert stop_target[idx].sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[idx] == mel_input[idx].shape[0]

                # check the second itme in the batch
                assert mel_input[1 - idx, -1].sum() == 0
                assert stop_target[1 - idx, -1] == 1
                assert len(mel_lengths.shape) == 1

                # check batch conditions
                assert (mel_input * stop_target.unsqueeze(2)).sum() == 0


class TestTTSDatasetMemory(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestTTSDatasetMemory, self).__init__(*args, **kwargs)
        self.max_loader_iter = 4
        self.ap = AudioProcessor(**c.audio)

    def test_loader(self):
        if ok_ljspeech:
            dataset = TTSDatasetMemory.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                c.r,
                c.text_cleaner,
                preprocessor = tts_cache,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # check mel-spec shape
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']
                assert mel_input.max() <= self.ap.max_norm
                # check mel-spec range.
                if self.ap.symmetric_norm:
                    assert mel_input.min() >= -self.ap.max_norm
                    assert mel_input.min() < 0, mel_input.min()
                else:
                    assert mel_input.min() >= 0
                # check mel-spec correctness
                mel_spec = mel_input[0].cpu().numpy()
                wav = self.ap.inv_melspectrogram(mel_spec)
                self.ap.save_wav(OUTPATH+'/mel_inv_TTSmemo.wav')

    def test_batch_group_shuffle(self):
        if ok_ljspeech:
            dataset = TTSDatasetMemory.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                c.r,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                batch_group_size=16,
                min_seq_len=c.min_seq_len)

            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=True,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            frames = dataset.items
            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                neg_values = text_input[text_input < 0]
                check_count = len(neg_values)
                assert check_count == 0, \
                    " !! Negative values in text_input: {}".format(check_count)
                # TODO: more assertion here
                assert mel_input.shape[0] == c.batch_size
                assert mel_input.shape[2] == c.audio['num_mels']
            dataloader.dataset.sort_items()
            assert frames[0] != dataloader.dataset.items[0]


    def test_padding(self):
        if ok_ljspeech:
            dataset = TTSDatasetMemory.MyDataset(
                c.data_path_cache,
                'tts_meta_data.csv',
                1,
                c.text_cleaner,
                preprocessor=ljspeech,
                ap=self.ap,
                min_seq_len=c.min_seq_len)

            # Test for batch size 1
            dataloader = DataLoader(
                dataset,
                batch_size=1,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=True,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                # linear_input = data[2]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                # check the last time step to be zero padded
                assert mel_input[0, -1].sum() == 0
                assert mel_input[0, -2].sum() != 0
                assert stop_target[0, -1] == 1
                assert stop_target[0, -2] == 0
                assert stop_target.sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[0] == mel_input[0].shape[0]

            # Test for batch size 2
            dataloader = DataLoader(
                dataset,
                batch_size=2,
                shuffle=False,
                collate_fn=dataset.collate_fn,
                drop_last=False,
                num_workers=c.num_loader_workers)

            for i, data in enumerate(dataloader):
                if i == self.max_loader_iter:
                    break
                text_input = data[0]
                text_lengths = data[1]
                mel_input = data[2]
                mel_lengths = data[3]
                stop_target = data[4]
                item_idx = data[5]

                if mel_lengths[0] > mel_lengths[1]:
                    idx = 0
                else:
                    idx = 1

                # check the first item in the batch
                assert mel_input[idx, -1].sum() == 0
                assert mel_input[idx, -2].sum() != 0, mel_input
                assert stop_target[idx, -1] == 1
                assert stop_target[idx, -2] == 0
                assert stop_target[idx].sum() == 1
                assert len(mel_lengths.shape) == 1
                assert mel_lengths[idx] == mel_input[idx].shape[0]

                # check the second itme in the batch
                assert mel_input[1 - idx, -1].sum() == 0
                assert stop_target[1 - idx, -1] == 1
                assert len(mel_lengths.shape) == 1

                # check batch conditions
                assert (mel_input * stop_target.unsqueeze(2)).sum() == 0

